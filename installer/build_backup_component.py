"""构建独立管理员组件；只嵌入明确白名单源码和最小标准库，不读取用户数据。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import backup_service as S
import config as C


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def release_component_version(app_version):
    """每个应用补丁版本预留 1000 个组件修订序号；发布唯一源仍为 APP_VERSION。"""
    from backup_component_auth import release_component_version as encode
    return encode(app_version)


def build(runtime=None, output=None, iscc=None, *, abi_probe=False, component_version=None, source_dir=None):
    component_version = release_component_version(C.APP_VERSION) if component_version is None else component_version
    if type(component_version) is not int or component_version < 1:
        raise ValueError("组件版本必须是单调递增的正整数。")
    runtime = Path(runtime or ROOT / "build/py312").absolute()
    output = Path(output or ROOT / "build/backup-component").absolute()
    source = Path(source_dir or ROOT / "src").absolute()
    files = {"code/" + name: source / name for name in S.MODULES}
    files.update({"runtime/" + name: path for name, path in S._runtime_files(runtime).items()})
    entries = [{"path": name, "sha256": sha(path), "size": path.stat().st_size}
               for name, path in sorted(files.items())]
    build_id = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
    fingerprints = {"build_id": build_id, "app_version": C.APP_VERSION, "component_version": component_version,
        "installer_sha256": sha(ROOT / "installer/backup_component.iss"),
        "builder_sha256": sha(Path(__file__))}
    source_hashes = {e["path"][5:]: e["sha256"] for e in entries if e["path"].startswith("code/")}
    runtime_hashes = {e["path"][8:]: e["sha256"] for e in entries if e["path"].startswith("runtime/")}
    executable, metadata = output / "backup_component.exe", output / "backup_component.json"
    if not abi_probe and executable.is_file() and metadata.is_file():
        try:
            cached = json.loads(metadata.read_text(encoding="utf-8"))
            if (cached.get("schema") == 1 and all(cached.get(k) == v for k, v in fingerprints.items())
                    and cached.get("source_hashes") == source_hashes
                    and cached.get("runtime_hashes") == runtime_hashes
                    and cached.get("files") == len(entries)
                    and cached.get("bytes") == sum(e["size"] for e in entries)
                    and cached.get("sha256") == sha(executable)):
                return {"executable": str(executable), "metadata": str(metadata), "cached": True, **cached}
        except (OSError, ValueError):
            pass  # 只重建固定产物；不删除旧目录、载荷或任何用户资料。
    stage = output / "stage" / build_id
    stage.mkdir(parents=True, exist_ok=True)
    template = stage / "payload-template.json"
    template.write_text(json.dumps({"schema": S.SCHEMA, "build_id": build_id,
                                  "component_version": component_version, "files": entries},
                                  indent=2), encoding="utf-8")
    files["payload-template.json"] = template
    install = []
    directives = []
    for index, (name, path) in enumerate(sorted(files.items())):
        # 重名的 __init__.py 等以固定编号提取；先拷到构建暂存，避免编译期间源码变化。
        filename = f"payload-{index:05d}.bin"
        target = stage / filename
        raw = path.read_bytes()
        target.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if name != "payload-template.json" and digest != next(x["sha256"] for x in entries if x["path"] == name):
            raise RuntimeError("构建期间文件变化：" + name)
        directives.append(f'Source: "{target}"; DestName: "{filename}"; Flags: dontcopy')
        install.append("  InstallFile('" + filename + "', '" + name.replace("/", "\\") + "', '" + digest + "');")
    (stage / "files.iss").write_text("\n".join(directives), encoding="utf-8-sig")
    (stage / "install.iss").write_text("\n".join(install), encoding="utf-8-sig")
    if not iscc:
        candidates = [Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") /
                      "Programs/Inno Setup 6/ISCC.exe", Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
                      Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")]
        iscc = next((p for p in candidates if p.is_file()), None)
    if not iscc or not Path(iscc).is_file():
        raise RuntimeError("构建备份组件需要 Inno Setup 6 的 ISCC.exe。")
    engine = Path(iscc).parent / "Setup.e32"
    image = engine.read_bytes()
    pe = struct.unpack_from("<I", image, 0x3c)[0]
    if image[pe:pe+4] != b"PE\0\0" or struct.unpack_from("<H", image, pe + 4)[0] != 0x14c:
        raise RuntimeError("组件安装脚本 Win32 ABI 只接受 Inno Setup 6 的 x86 Setup.e32。")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(iscc), "/Qp", "/DStage=" + str(stage), "/DOutput=" + str(output),
               "/DBuildID=" + build_id, "/DAppVersion=" + C.APP_VERSION,
               str(ROOT / "installer/backup_component.iss")]
    if abi_probe:
        command.insert(-1, "/DABIProbe=1")
    result = subprocess.run(command, capture_output=True, creationflags=S.NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stdout.decode("utf-8", errors="replace") +
                           result.stderr.decode("utf-8", errors="replace"))
    executable = output / ("backup_component_abi_probe.exe" if abi_probe else "backup_component.exe")
    if abi_probe:
        return {"executable": str(executable), "abi_probe_only": True, "sha256": sha(executable)}
    if any(sha(source / name) != digest for name, digest in source_hashes.items()):
        raise RuntimeError("组件源码在编译期间发生变化，未发布组件清单，请重新构建。")
    meta = {"schema": 1, **fingerprints, "sha256": sha(executable),
            "files": len(entries), "bytes": sum(e["size"] for e in entries),
            "source_hashes": source_hashes, "runtime_hashes": runtime_hashes}
    (output / "backup_component.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"executable": str(executable), "metadata": str(output / "backup_component.json"), **meta}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime")
    parser.add_argument("--output")
    parser.add_argument("--iscc")
    parser.add_argument("--abi-probe", action="store_true")
    parser.add_argument("--component-version", type=int)
    args = parser.parse_args()
    print(json.dumps(build(args.runtime, args.output, args.iscc, abi_probe=args.abi_probe,
                           component_version=args.component_version), ensure_ascii=False, indent=2))
