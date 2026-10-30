"""仅发布者运行：生成用户 DPAPI 私钥，或为已核对的组件清单签名。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import backup_component_auth as A


def main(argv=None):
    parser = argparse.ArgumentParser(description="PaperPiggy 后台组件离线签名")
    parser.add_argument("--init-key", action="store_true")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--public-output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.init_key:
        if not args.public_output or args.manifest or args.output:
            parser.error("--init-key 只与 --public-output、可选 --key-file 一起使用")
        if args.public_output.exists():
            parser.error("公钥输出文件已存在，禁止覆盖")
        public = A.initialize_key(args.key_file)
        args.public_output.parent.mkdir(parents=True, exist_ok=True)
        with args.public_output.open("x", encoding="utf-8") as stream:
            json.dump(public, stream, sort_keys=True, indent=2)
            stream.write("\n")
        print("已生成本用户 DPAPI 发布密钥；公开指纹：" + public["key_id"])
        return 0
    if not args.manifest or not args.output or args.public_output:
        parser.error("签名需要 --manifest 和 --output")
    manifest = A.strict_json(args.manifest.read_bytes())
    signed = A.sign_with_key_file(manifest, args.key_file)
    # 发布自检使用从本次信封读到的 key_id 不足以验签：仍使用固定公开 pin。
    A.verify_envelope(signed)
    with args.output.open("xb") as stream:
        stream.write(signed)
    print("组件清单签名及固定公钥自检通过。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (A.ComponentAuthError, OSError) as exc:
        print("组件签名失败：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
