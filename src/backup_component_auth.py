"""后台组件清单的严格解析及 Windows CNG RSA3072/PSS 验签。

此模块不联网、不提权、不执行组件。生产验签只使用随受保护入口固定的
backup_component_trust；public_key 参数仅供离线发布自检及隔离测试。
"""
from contextlib import contextmanager
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

SCHEMA = 1
PRODUCT = "PaperPiggyBackupComponent"
ALGORITHM = "RSA3072-SHA256-PSS-SALT32"
DOMAIN = b"PaperPiggy.BackupComponent.Manifest.v1\x00"
MAX_ENVELOPE = 8 * 1024 * 1024
MAX_HELPER_SIZE = 8 * 1024 * 1024 * 1024
MAX_VERSION = (1 << 53) - 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SEMVER = re.compile(r"(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z")
_FIELDS = {"schema", "product", "component_version", "app_version", "helper_sha256",
           "helper_size", "build_id", "source_hashes", "runtime_hashes"}
_KEY_MAGIC = b"PPBCKEY1\x00"


class ComponentAuthError(ValueError):
    pass


def release_component_version(app_version):
    """正式组件基础版本；低三位留给隔离验收 revision，不进入正式包。"""
    if not isinstance(app_version, str) or not _SEMVER.fullmatch(app_version):
        raise ComponentAuthError("应用版本必须是规范的三段数字版本。")
    major, minor, patch = map(int, app_version.split("."))
    if any(value > 999 for value in (major, minor, patch)):
        raise ComponentAuthError("应用版本各段必须在 0..999，防止组件版本编码碰撞。")
    return (major * 1000000 + minor * 1000 + patch) * 1000


def _fail(message):
    raise ComponentAuthError(message)


def _unique(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            _fail("组件清单含重复 JSON 字段。")
        output[key] = value
    return output


def strict_json(raw):
    if not isinstance(raw, (str, bytes, bytearray)):
        _fail("组件清单必须是 JSON 文本。")
    try:
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "strict")
        if len(raw) > MAX_ENVELOPE:
            _fail("组件清单过大。")
        return json.loads(bytes(raw).decode("utf-8", "strict"), object_pairs_hook=_unique,
                          parse_constant=lambda _: _fail("组件清单不允许非有限数值。"),
                          parse_float=lambda _: _fail("组件清单不允许小数。"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ComponentAuthError("组件清单 JSON 无效。") from exc


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("ascii")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ComponentAuthError("组件清单无法规范化。") from exc


def _integer(value, lower, upper, label):
    if type(value) is not int or not lower <= value <= upper:
        _fail("组件清单的" + label + "无效。")


def _hash(value, label):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _fail("组件清单的" + label + "无效。")


def _fingerprints(value, label):
    if type(value) is not dict or not 1 <= len(value) <= 20000:
        _fail("组件清单的" + label + "无效。")
    seen = set()
    for name, digest in value.items():
        if (not isinstance(name, str) or not 1 <= len(name) <= 1024
                or "\\" in name or ":" in name or name.startswith("/")
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name)):
            _fail("组件文件路径无效。")
        for part in name.split("/"):
            stem = part.split(".")[0].upper()
            if (not part or part in (".", "..") or part.endswith((" ", "."))
                    or any(c in part for c in '<>"|?*')
                    or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                    or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)):
                _fail("组件文件路径无效。")
        folded = name.casefold()
        if folded in seen:
            _fail("组件文件路径有大小写冲突。")
        seen.add(folded)
        _hash(digest, label)


def validate_manifest(manifest, minimum_version=None):
    if type(manifest) is not dict or set(manifest) != _FIELDS:
        _fail("组件清单字段不符合协议。")
    if type(manifest["schema"]) is not int or manifest["schema"] != SCHEMA:
        _fail("组件清单协议版本不受支持。")
    if manifest["product"] != PRODUCT:
        _fail("组件清单不属于 PaperPiggy。")
    _integer(manifest["component_version"], 1, MAX_VERSION, "组件版本")
    if minimum_version is not None:
        _integer(minimum_version, 1, MAX_VERSION, "已安装版本")
        if manifest["component_version"] < minimum_version:
            _fail("组件更新版本倒退，已拒绝。")
    if not isinstance(manifest["app_version"], str) or not _SEMVER.fullmatch(manifest["app_version"]):
        _fail("组件清单应用版本无效。")
    _integer(manifest["helper_size"], 1, MAX_HELPER_SIZE, "安装器大小")
    _hash(manifest["helper_sha256"], "安装器指纹")
    _hash(manifest["build_id"], "构建指纹")
    _fingerprints(manifest["source_hashes"], "源码指纹")
    _fingerprints(manifest["runtime_hashes"], "运行时指纹")
    # 返回独立对象，调用方不能在已核验的引用外原地改动输入。
    return strict_json(canonical_json(manifest))


def _b64(raw, label):
    if not isinstance(raw, str) or len(raw) > MAX_ENVELOPE:
        _fail(label + "编码无效。")
    try:
        data = base64.b64decode(raw, validate=True)
        if base64.b64encode(data).decode("ascii") != raw:
            _fail(label + "编码不是规范形式。")
        return data
    except (ValueError, UnicodeError) as exc:
        raise ComponentAuthError(label + "编码无效。") from exc


def validate_public_key(blob):
    if not isinstance(blob, bytes) or len(blob) < 24:
        _fail("组件公钥格式无效。")
    magic, bits, exp_size, modulus_size, p1, p2 = struct.unpack("<6I", blob[:24])
    if (magic != 0x31415352 or bits != 3072 or exp_size != 3 or modulus_size != 384
            or p1 or p2 or len(blob) != 24 + exp_size + modulus_size
            or blob[24:27] != b"\x01\x00\x01" or not blob[27] & 0x80
            or not blob[-1] & 1):
        _fail("组件公钥必须是 RSA3072/65537。")
    return blob


def key_id(public_key):
    return hashlib.sha256(validate_public_key(public_key)).hexdigest()


def pinned_public_key():
    import backup_component_trust as trust
    public = validate_public_key(_b64(trust.PUBLIC_KEY_B64, "固定公钥"))
    if key_id(public) != trust.KEY_ID:
        _fail("固定组件公钥指纹不一致。")
    return public


def _check(status):
    if status != 0:
        _fail("Windows CNG 加密操作失败（0x%08x）。" % (status & 0xffffffff))


def _bcrypt():
    if sys.platform != "win32":
        _fail("组件签名需要 Windows CNG。")
    dll = ctypes.WinDLL("bcrypt", use_last_error=True)
    ptr, u32 = ctypes.c_void_p, ctypes.c_uint32
    definitions = {
        "BCryptOpenAlgorithmProvider": ([ctypes.POINTER(ptr), ctypes.c_wchar_p, ctypes.c_wchar_p, u32]),
        "BCryptCloseAlgorithmProvider": ([ptr, u32]),
        "BCryptGenerateKeyPair": ([ptr, ctypes.POINTER(ptr), u32, u32]),
        "BCryptFinalizeKeyPair": ([ptr, u32]),
        "BCryptDestroyKey": ([ptr]),
        "BCryptExportKey": ([ptr, ptr, ctypes.c_wchar_p, ptr, u32, ctypes.POINTER(u32), u32]),
        "BCryptImportKeyPair": ([ptr, ptr, ctypes.c_wchar_p, ctypes.POINTER(ptr), ptr, u32, u32]),
        "BCryptSignHash": ([ptr, ptr, ptr, u32, ptr, u32, ctypes.POINTER(u32), u32]),
        "BCryptVerifySignature": ([ptr, ptr, ptr, u32, ptr, u32, u32]),
    }
    for name, arguments in definitions.items():
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, ctypes.c_int32
    return dll


@contextmanager
def _key(blob=None, private=False):
    dll, algorithm, handle = _bcrypt(), ctypes.c_void_p(), ctypes.c_void_p()
    _check(dll.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "RSA", None, 0))
    try:
        if blob is None:
            _check(dll.BCryptGenerateKeyPair(algorithm, ctypes.byref(handle), 3072, 0))
            _check(dll.BCryptFinalizeKeyPair(handle, 0))
        else:
            if not isinstance(blob, (bytes, bytearray)) or not 24 <= len(blob) <= 4096:
                _fail("组件密钥格式无效。")
            buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
            try:
                _check(dll.BCryptImportKeyPair(algorithm, None,
                    "RSAFULLPRIVATEBLOB" if private else "RSAPUBLICBLOB",
                    ctypes.byref(handle), buffer, len(blob), 0))
            finally:
                ctypes.memset(buffer, 0, len(buffer))
        yield dll, handle
    finally:
        if handle:
            dll.BCryptDestroyKey(handle)
        dll.BCryptCloseAlgorithmProvider(algorithm, 0)


def _export(dll, handle, kind):
    size = ctypes.c_uint32()
    _check(dll.BCryptExportKey(handle, None, kind, None, 0, ctypes.byref(size), 0))
    data = bytearray(size.value)
    buffer = (ctypes.c_ubyte * len(data)).from_buffer(data)
    _check(dll.BCryptExportKey(handle, None, kind, buffer, len(data), ctypes.byref(size), 0))
    return data


def generate_keypair():
    """仅显式发布工具/测试调用；私钥返回可清零的 bytearray，不写磁盘。"""
    with _key() as (dll, handle):
        public = bytes(_export(dll, handle, "RSAPUBLICBLOB"))
        private = _export(dll, handle, "RSAFULLPRIVATEBLOB")
    return private, validate_public_key(public)


class _Pss(ctypes.Structure):
    _fields_ = [("pszAlgId", ctypes.c_wchar_p), ("cbSalt", ctypes.c_uint32)]


def _sign(data, private_key):
    digest = ctypes.create_string_buffer(hashlib.sha256(data).digest())
    padding, size = _Pss("SHA256", 32), ctypes.c_uint32()
    with _key(private_key, private=True) as (dll, handle):
        public = bytes(_export(dll, handle, "RSAPUBLICBLOB"))
        _check(dll.BCryptSignHash(handle, ctypes.byref(padding), digest, 32,
                                None, 0, ctypes.byref(size), 8))
        signature = ctypes.create_string_buffer(size.value)
        _check(dll.BCryptSignHash(handle, ctypes.byref(padding), digest, 32,
                                signature, len(signature), ctypes.byref(size), 8))
        return signature.raw[:size.value], public


def public_from_private(private_key):
    with _key(private_key, private=True) as (dll, handle):
        return validate_public_key(bytes(_export(dll, handle, "RSAPUBLICBLOB")))


def _verify(data, signature, public_key):
    validate_public_key(public_key)
    if len(signature) != 384:
        _fail("组件签名长度无效。")
    digest = ctypes.create_string_buffer(hashlib.sha256(data).digest())
    encoded = ctypes.create_string_buffer(signature)
    padding = _Pss("SHA256", 32)
    with _key(public_key) as (dll, handle):
        result = dll.BCryptVerifySignature(handle, ctypes.byref(padding), digest, 32,
                                           encoded, len(signature), 8)
    if result != 0:
        _fail("组件签名校验失败。")


def sign_manifest(manifest, private_key):
    manifest = validate_manifest(manifest)
    public = public_from_private(private_key)
    unsigned = {"algorithm": ALGORITHM, "key_id": key_id(public), "manifest": manifest}
    signature, _ = _sign(DOMAIN + canonical_json(unsigned), private_key)
    return canonical_json({**unsigned, "signature": base64.b64encode(signature).decode("ascii")})


def verify_envelope(raw, public_key=None, minimum_version=None):
    envelope = strict_json(raw)
    if type(envelope) is not dict or set(envelope) != {"algorithm", "key_id", "manifest", "signature"}:
        _fail("组件签名信封字段不符合协议。")
    if envelope["algorithm"] != ALGORITHM:
        _fail("组件签名算法不受支持。")
    public = pinned_public_key() if public_key is None else validate_public_key(public_key)
    if envelope["key_id"] != key_id(public):
        _fail("组件签名使用了未知公钥。")
    manifest = validate_manifest(envelope["manifest"], minimum_version=minimum_version)
    unsigned = {name: envelope[name] for name in ("algorithm", "key_id", "manifest")}
    _verify(DOMAIN + canonical_json(unsigned), _b64(envelope["signature"], "组件签名"), public)
    return manifest


def default_key_path():
    directory = os.environ.get("LOCALAPPDATA")
    if not directory or not Path(directory).is_absolute():
        _fail("无法确定发布者本地密钥目录。")
    return Path(directory) / "PaperPiggyRelease/component-key.dpapi"


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


def _dpapi(data, decrypt=False):
    if sys.platform != "win32":
        _fail("发布私钥保护需要 Windows DPAPI。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    source_data = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = _Blob(len(data), ctypes.addressof(source_data)), _Blob()
    name = "CryptUnprotectData" if decrypt else "CryptProtectData"
    function = getattr(crypt, name)
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p if decrypt else ctypes.c_wchar_p,
                         ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    try:
        # UI_FORBIDDEN，刻意不含 LOCAL_MACHINE，只有当前登录用户可解密。
        if not function(ctypes.byref(source), None if decrypt else "PaperPiggy component release key",
                        None, None, None, 1, ctypes.byref(output)):
            _fail("无法" + ("解密" if decrypt else "保护") + "发布私钥。")
        return bytearray(ctypes.string_at(output.data, output.size))
    finally:
        ctypes.memset(source_data, 0, len(source_data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)


def _safe_key_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    if str(path).startswith(("\\\\?\\", "\\\\.\\", "\\\\")):
        _fail("发布密钥必须使用本机普通绝对路径。")
    # 私钥（即使已加密）不得写进开发仓库、构建素材或安装包。
    repository = Path(os.path.realpath(__file__)).parents[1]
    final_path = Path(os.path.realpath(path))
    if final_path == repository or repository in final_path.parents:
        _fail("发布私钥必须存放在仓库之外。")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400 or item.is_symlink():
            _fail("发布密钥路径不能含重解析点。")
        if item == path and info.st_nlink != 1:
            _fail("发布密钥不能是硬链接。")
    return path


def _protect_path(path, directory=False):
    """受保护 DACL：仅当前用户与 SYSTEM，避免父目录的宽泛继承。"""
    adv, kernel = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    token, needed = ctypes.c_void_p(), ctypes.c_uint32()
    adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                      ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        _fail("无法读取发布用户身份。")
    sid_string = ctypes.c_void_p()
    try:
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 1 <= needed.value <= 65536:
            _fail("发布用户令牌大小无效。")
        identity = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, identity, len(identity), ctypes.byref(needed)):
            _fail("无法读取发布用户身份。")
        if not adv.ConvertSidToStringSidW(ctypes.c_void_p.from_buffer(identity).value,
                                        ctypes.byref(sid_string)):
            _fail("无法读取发布用户身份。")
        sid = ctypes.wstring_at(sid_string)
    finally:
        if sid_string:
            kernel.LocalFree(sid_string)
        kernel.CloseHandle(token)
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    adv.SetFileSecurityW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = adv.SetFileSecurityW.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    inherit = "OICI" if directory else ""
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)", 1, ctypes.byref(pointer), ctypes.byref(length)):
        _fail("无法建立发布私钥目录权限。")
    try:
        if not adv.SetFileSecurityW(str(path), 0x80000004, pointer):
            _fail("无法保护发布私钥目录。")
    finally:
        kernel.LocalFree(pointer)


def initialize_key(key_file=None):
    path = _safe_key_path(key_file or default_key_path())
    if path.exists():
        _fail("发布私钥已存在，禁止覆盖。")
    new_directory = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_key_path(path)
    if new_directory:
        _protect_path(path.parent, directory=True)
    private, public = generate_keypair()
    try:
        encrypted = _dpapi(private)
        with path.open("xb") as stream:
            _protect_path(path)
            stream.write(_KEY_MAGIC)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        private[:] = b"\x00" * len(private)
    return {"schema": 1, "algorithm": ALGORITHM, "key_id": key_id(public),
            "public_key_b64": base64.b64encode(public).decode("ascii")}


def sign_with_key_file(manifest, key_file=None, public_key=None):
    path = _safe_key_path(key_file or default_key_path())
    if not path.is_file() or path.stat().st_size > 16384:
        _fail("发布私钥文件无效。")
    encrypted = path.read_bytes()
    if not encrypted.startswith(_KEY_MAGIC):
        _fail("发布私钥文件格式无效。")
    private = _dpapi(encrypted[len(_KEY_MAGIC):], decrypt=True)
    try:
        expected = pinned_public_key() if public_key is None else validate_public_key(public_key)
        if public_from_private(private) != expected:
            _fail("发布私钥与固定组件公钥不匹配，禁止签名。")
        envelope = sign_manifest(manifest, private)
        verify_envelope(envelope, public_key=expected)
        return envelope
    finally:
        private[:] = b"\x00" * len(private)
