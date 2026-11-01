"""普通 ZIP 分包组成的完整恢复点；总清单是最后提交的完成标记。

物理分包使用格式 3 标记，原文件视图继续使用格式 2 清单。调用者无需
把数据库、WAL 或超大文件先写成另一份完整 ZIP，读取时按段流式重组。
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from backup_jobs import BackupError, BackupCancelled, check_path, io_path

MAX_PART_SIZE = 5_000_000_000
MIN_PART_SIZE = 4096  # Only small isolated tests use a limit below the product limit.
MAX_INDEX_SIZE = 256 * 1024 * 1024
MAX_PARTS = 10000
MAX_SEGMENTS = 1000000
MARKER_LIMIT = 64 * 1024
CHUNK = 1024 * 1024


def _A():
    import backup_archive
    return backup_archive


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(raw, label):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise BackupError(f"{label}含重复字段。")
            output[key] = value
        return output
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise BackupError(f"{label}损坏。") from exc
    if not isinstance(result, dict):
        raise BackupError(f"{label}格式无效。")
    return result


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _basename(value):
    if not isinstance(value, str) or not value or _A().safe_name(value) != value or "/" in value:
        raise BackupError("分包文件名必须是同目录的安全文件名。")
    return value


def _stem(index_name):
    _basename(index_name)
    if not index_name.endswith(".backup.json"):
        raise BackupError("整套备份总清单名称无效。")
    return index_name[:-len(".backup.json")]


def _part_name(index_name, number):
    return f"{_stem(index_name)}.part{number:04d}.zip"


def _identity(path):
    s = Path(io_path(path)).stat()
    return s.st_dev, s.st_ino


def _snapshot(path):
    s = Path(io_path(path)).stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def _marker(backup, number, index_name):
    return {"format": 3, "backup_id": backup["backup_id"], "job_id": backup["job_id"],
            "machine_id": backup["machine_id"], "kind": backup["kind"],
            "part_number": number, "index_name": index_name}


def _zip_info(name):
    zi = zipfile.ZipInfo(name)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = (stat.S_IFREG | 0o600) << 16
    return zi


def _entry_bound(size, name):
    # zlib's conservative deflate bound (larger than its default-parameter bound)
    # plus both ZIP headers, UTF-8 name, ZIP64 extra fields and descriptor.
    encoded = len(name.encode("utf-8"))
    return size + ((size + 7) >> 3) + ((size + 63) >> 6) + 128 + 256 + 2 * encoded


def _directory_bound(z):
    # ZipInfo.extra may not contain the ZIP64 central extra yet; reserve it too.
    return 128 + sum(128 + len(x.filename.encode("utf-8")) + len(x.extra) + len(x.comment)
                     for x in z.infolist())


def _payload_limit(limit, name):
    low, high = 0, limit
    while low < high:
        mid = (low + high + 1) // 2
        if _entry_bound(mid, name) + 2048 <= limit:
            low = mid
        else:
            high = mid - 1
    if not low:
        raise BackupError("分包上限不足以容纳文件路径与 ZIP 元数据。")
    return low


class _SegmentStream(io.RawIOBase):
    def __init__(self, owner, segments):
        super().__init__()
        self.owner = owner
        self.segments = segments
        self.position = 0
        self.current = None
        self.digest = None
        self.count = 0

    def readable(self):
        return True

    def _start(self):
        if self.position >= len(self.segments):
            return False
        self.owner._cancel()
        segment = self.segments[self.position]
        self.current = self.owner.archives[segment["part_number"] - 1].open(segment["member"])
        self.digest = hashlib.sha256()
        self.count = 0
        return True

    def _finish(self):
        segment = self.segments[self.position]
        if self.count != segment["size"] or self.digest.hexdigest() != segment["sha256"]:
            raise BackupError(f"文件片段校验失败：{segment['member']}")
        self.current.close()
        self.current = None
        self.position += 1

    def read(self, size=-1):
        if self.closed:
            raise ValueError("read of closed file")
        if size is None or size < 0:
            # ZipFile-compatible convenience, not used for large-file restoration.
            pieces = []
            while piece := self.read(CHUNK):
                pieces.append(piece)
            return b"".join(pieces)
        if not size:
            return b""
        output = bytearray()
        while len(output) < size:
            self.owner._cancel()
            if self.current is None and not self._start():
                break
            piece = self.current.read(min(size - len(output), CHUNK))
            if not piece:
                self._finish()
                continue
            self.count += len(piece)
            self.digest.update(piece)
            output.extend(piece)
        return bytes(output)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def close(self):
        if self.current is not None:
            self.current.close()
            self.current = None
        super().close()


class MultipartReader:
    """Validated physical set exposing the original logical ZipFile interface."""
    def __init__(self, path, cancel=None, *, pending=False):
        self.cancel = cancel
        self.stack = ExitStack()
        self.archives = []
        self._closed = False
        try:
            self._load(path, pending)
        except Exception:
            self.close()
            raise

    def _cancel(self):
        _A()._cancel(self.cancel)

    def _load(self, path, pending):
        selected = check_path(path, allow_missing=False)
        selected_marker = None
        if selected.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io_path(selected)) as z:
                selected_marker = read_marker(z)
            index_name = _basename(selected_marker["index_name"])
            index_path = check_path(selected.parent / index_name)
            if not Path(io_path(index_path)).is_file():
                raise BackupError(f"缺少整套备份总清单：{index_name}；请将总清单与全部分包放在同一目录。")
        elif selected.name.endswith(".backup.json") or pending and selected.name.endswith(".backup.json.part"):
            index_path = selected
            index_name = selected.name[:-5] if pending else selected.name
        else:
            raise BackupError("请选择一个分包 ZIP 或完整备份总清单。")
        self.index_path = index_path
        if Path(io_path(index_path)).stat().st_size > MAX_INDEX_SIZE:
            raise BackupError("整套备份总清单过大。")
        index_before = _snapshot(index_path)
        raw = Path(io_path(index_path)).read_bytes()
        index = _read_json(raw, "整套备份总清单")
        if index.get("format") != 3 or index.get("index_name") != index_name:
            raise BackupError("整套备份总清单格式或名称不匹配。")
        self.manifest = index.get("manifest")
        if not isinstance(self.manifest, dict) or self.manifest.get("format") != 2:
            raise BackupError("整套备份缺少原文件清单。")
        self.manifest_bytes = _json(self.manifest)
        if len(self.manifest_bytes) > _A().MAX_MANIFEST:
            raise BackupError("原文件清单过大。")
        backup_id = self.manifest.get("backup_id")
        if not re.fullmatch(r"[a-f0-9]{32}", str(backup_id or "")) or index.get("backup_id") != backup_id:
            raise BackupError("整套备份身份不匹配。")
        if not index_name.startswith("PaperPiggy-") or not _stem(index_name).endswith("-" + backup_id):
            raise BackupError("整套备份文件名与身份不匹配。")
        limit = index.get("max_part_size")
        if type(limit) is not int or not MIN_PART_SIZE <= limit <= MAX_PART_SIZE:
            raise BackupError("整套备份的分包上限无效。")
        parts = index.get("parts")
        mapping = index.get("file_segments")
        files = self.manifest.get("files")
        if (not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS
                or not isinstance(mapping, dict) or not isinstance(files, list)
                or len(files) > _A().MAX_ENTRIES or len(mapping) != len(files)):
            raise BackupError("整套备份的分包或文件清单无效。")
        self.mapping = mapping
        self.parts = []
        seen_files = set()
        declared_members = [dict() for _ in parts]
        segment_count = 0
        for item in files:
            if not isinstance(item, dict):
                raise BackupError("原文件清单无效。")
            name = _A().safe_name(item.get("path"))
            if name.casefold() in seen_files:
                raise BackupError("原文件清单含重复路径。")
            seen_files.add(name.casefold())
            segments = mapping.get(name)
            if not isinstance(segments, list) or not segments:
                raise BackupError(f"缺少原文件片段清单：{name}")
            position = 0
            for number, segment in enumerate(segments):
                segment_count += 1
                if segment_count > MAX_SEGMENTS or not isinstance(segment, dict):
                    raise BackupError("文件片段清单超过安全上限或格式无效。")
                part_number = segment.get("part_number")
                size = segment.get("size")
                member = _A().safe_name(segment.get("member"))
                if (type(part_number) is not int or not 1 <= part_number <= len(parts)
                        or type(size) is not int or size < 0 or size > limit
                        or segment.get("offset") != position or type(segment.get("offset")) is not int
                        or not _hash(segment.get("sha256"))):
                    raise BackupError(f"文件片段大小、顺序或哈希无效：{name}")
                if member != name and not re.fullmatch(r"fragments/[0-9]{8}/[0-9]{8}", member):
                    raise BackupError("文件片段路径不在声明范围。")
                if len(segments) > 1 and member == name:
                    raise BackupError("多片段文件不能重复使用原文件路径。")
                members = declared_members[part_number - 1]
                folded = member.casefold()
                if folded in members:
                    raise BackupError("分包含重复或冲突的片段声明。")
                members[folded] = segment
                position += size
            if type(item.get("size")) is not int or position != item["size"]:
                raise BackupError(f"文件片段总大小与原文件不一致：{name}")
        if set(mapping) != {item["path"] for item in files}:
            raise BackupError("整套备份有清单外的原文件映射。")
        selected_number = None
        for offset, part in enumerate(parts, 1):
            self._cancel()
            if (not isinstance(part, dict) or part.get("part_number") != offset
                    or type(part.get("part_number")) is not int
                    or part.get("name") != _part_name(index_name, offset)
                    or type(part.get("size")) is not int or not 0 < part["size"] <= limit
                    or not _hash(part.get("sha256"))):
                raise BackupError("分包序号、名称、大小或哈希无效。")
            part_path = check_path(index_path.parent / part["name"])
            if not Path(io_path(part_path)).is_file():
                raise BackupError(f"缺少分包：{part['name']}")
            if selected_marker is not None and part_path == selected:
                selected_number = offset
            before = _snapshot(part_path)
            if before[2] != part["size"] or _A().file_sha256(part_path, self.cancel) != part["sha256"]:
                raise BackupError(f"分包损坏或内容被更换：{part['name']}")
            z = self.stack.enter_context(zipfile.ZipFile(io_path(part_path)))
            if read_marker(z) != _marker(self.manifest, offset, index_name):
                raise BackupError(f"分包不是同一套备份：{part['name']}")
            infos = z.infolist()
            if len(infos) > _A().MAX_ENTRIES + 1:
                raise BackupError("分包成员数量超过安全上限。")
            seen_members = set()
            for info in infos:
                name = _A().safe_name(info.filename)
                folded = name.casefold()
                mode = (info.external_attr >> 16) & 0xFFFF
                if folded in seen_members or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.flag_bits & 1:
                    raise BackupError(f"分包含重复、链接、特殊或加密成员：{part['name']}")
                seen_members.add(folded)
                if name == "manifest.json":
                    continue
                entry = declared_members[offset - 1].get(folded)
                if entry is None or entry["member"] != name or info.file_size != entry["size"]:
                    raise BackupError(f"分包包含清单外成员或大小不符：{name}")
            if seen_members != set(declared_members[offset - 1]) | {"manifest.json"}:
                raise BackupError(f"分包缺少声明成员：{part['name']}")
            if _snapshot(part_path) != before:
                raise BackupError(f"校验期间分包发生变化：{part['name']}")
            self.archives.append(z)
            self.parts.append({"path": str(part_path), "size": part["size"], "sha256": part["sha256"]})
        if selected_marker is not None and (selected_number is None
                or selected_marker != _marker(self.manifest, selected_number, index_name)):
            raise BackupError("所选 ZIP 不属于该套备份。")
        if _snapshot(index_path) != index_before:
            raise BackupError("校验期间总清单发生变化。")
        self.package_info = {"archive_format": 3, "parts": self.parts,
                             "index_path": str(index_path), "index_size": len(raw),
                             "index_sha256": hashlib.sha256(raw).hexdigest(),
                             "part_count": len(self.parts),
                             "total_size": len(raw) + sum(x["size"] for x in self.parts)}
        self._infos = {}
        for item in files:
            zi = _zip_info(item["path"])
            zi.file_size = item["size"]
            self._infos[zi.filename] = zi
        zi = _zip_info("manifest.json")
        zi.file_size = len(self.manifest_bytes)
        self._infos[zi.filename] = zi

    def infolist(self):
        return list(self._infos.values())

    def namelist(self):
        return list(self._infos)

    def getinfo(self, name):
        return self._infos[name]

    def open(self, name, mode="r", *args, **kwargs):
        if self._closed or mode not in ("r", "rb"):
            raise BackupError("分包读取器仅允许只读访问。")
        if isinstance(name, zipfile.ZipInfo):
            name = name.filename
        if name == "manifest.json":
            return io.BytesIO(self.manifest_bytes)
        if name not in self.mapping:
            raise KeyError(name)
        return _SegmentStream(self, self.mapping[name])

    def read(self, name, *args, **kwargs):
        with self.open(name) as f:
            return f.read()

    def close(self):
        if not self._closed:
            self._closed = True
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_marker(z):
    try:
        info = z.getinfo("manifest.json")
        if info.file_size > MARKER_LIMIT:
            raise BackupError("分包身份标记过大。")
        marker = _read_json(z.read(info), "分包身份标记")
        if marker.get("format") != 3 or type(marker.get("part_number")) is not int:
            raise BackupError("不是有效的格式 3 分包。")
        _basename(marker.get("index_name"))
        return marker
    except (KeyError, zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise BackupError("分包身份标记缺失或损坏。") from exc


@contextmanager
def open_archive(path, cancel=None):
    path = check_path(path, allow_missing=False)
    if path.name.endswith(".backup.json"):
        with MultipartReader(path, cancel) as reader:
            yield reader
        return
    try:
        z = zipfile.ZipFile(io_path(path))
        try:
            info = z.getinfo("manifest.json")
            if info.file_size > _A().MAX_MANIFEST:
                raise BackupError("备份清单过大。")
            value = _read_json(z.read(info), "备份清单")
            physical_format = value.get("format")
            if physical_format == 2:
                yield z
                return
            if physical_format != 3:
                raise BackupError("不是受支持的完整备份格式。")
        finally:
            z.close()
        with MultipartReader(path, cancel) as reader:
            yield reader
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, EOFError) as exc:
        if isinstance(exc, (BackupError, BackupCancelled)):
            raise
        raise BackupError(f"备份包无法完整读取：{exc}") from exc


def archive_files(path, cancel=None):
    """Verify physical ownership/integrity without unpacking files or databases."""
    with open_archive(path, cancel) as reader:
        manifest = _A()._validate_structure(reader)
        if isinstance(reader, MultipartReader):
            return {"manifest": manifest, **reader.package_info}
        path = check_path(path, allow_missing=False)
        size = path.stat().st_size
        return {"manifest": manifest, "archive_format": 2,
                "parts": [{"path": str(path), "size": size, "sha256": _A().file_sha256(path, cancel)}],
                "index_path": "", "index_size": 0, "index_sha256": "",
                "part_count": 1, "total_size": size}


def write_set(destination, items, manifest, progress=None, cancel=None, work_dir=None,
              max_part_size=MAX_PART_SIZE):
    A = _A()
    if type(max_part_size) is not int or not MIN_PART_SIZE <= max_part_size <= MAX_PART_SIZE:
        raise BackupError("分包上限必须在安全测试下限与5GB之间。")
    A._cancel(cancel)
    created = manifest["created_at"]
    stem = f"PaperPiggy-{manifest['kind']}-{created[:19].replace(':', '').replace('-', '')}-{manifest['backup_id']}"
    index_name = stem + ".backup.json"
    index_path = destination / index_name
    pending_index = index_path.with_name(index_path.name + ".part")
    owned = {}
    parts = []
    mapping = {}
    z = None
    part_path = None
    directory_budget = 0

    def own(path):
        owned[path] = _identity(path)

    def new_part():
        nonlocal z, part_path, directory_budget
        if len(parts) >= MAX_PARTS:
            raise BackupError("分包数量超过单套安全上限。")
        number = len(parts) + 1
        part_path = destination / _part_name(index_name, number)
        z = zipfile.ZipFile(io_path(part_path), "x", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)
        own(part_path)
        z.writestr(_zip_info("manifest.json"), _json(_marker(manifest, number, index_name)))
        directory_budget = _directory_bound(z)

    def finish_part():
        nonlocal z
        if z is None:
            return
        z.close()
        z = None
        with Path(io_path(part_path)).open("r+b") as f:
            os.fsync(f.fileno())
        size = Path(io_path(part_path)).stat().st_size
        if size > max_part_size:
            raise BackupError("分包最终大小超过5GB限制，未登记本次备份。")
        parts.append({"part_number": len(parts) + 1, "name": part_path.name,
                      "size": size, "sha256": A.file_sha256(part_path, cancel)})

    try:
        new_part()
        for index, item in enumerate(items):
            A._cancel(cancel)
            size = item["size"]
            normal_name = item["path"]
            normal_limit = _payload_limit(max_part_size, normal_name)
            fragmented = size > normal_limit
            piece_limit = (_payload_limit(max_part_size, f"fragments/{index:08d}/00000000")
                           if fragmented else normal_limit)
            before = item["file"].stat() if item["payload"] is None else None
            source = (io.BytesIO(item["payload"]) if item["payload"] is not None
                      else item["file"].open("rb"))
            digest = hashlib.sha256()
            total = 0
            segments = []
            with source:
                while total < size or not segments:
                    A._cancel(cancel)
                    amount = min(piece_limit, size - total)
                    name = (f"fragments/{index:08d}/{len(segments):08d}" if fragmented else normal_name)
                    bound = _entry_bound(amount, name)
                    if z.fp.tell() + directory_budget + bound > max_part_size:
                        finish_part()
                        new_part()
                    if z.fp.tell() + directory_budget + bound > max_part_size:
                        raise BackupError("分包上限不足以容纳文件路径与 ZIP 元数据。")
                    piece_digest = hashlib.sha256()
                    count = 0
                    with z.open(_zip_info(name), "w", force_zip64=True) as dst:
                        while count < amount:
                            A._cancel(cancel)
                            data = source.read(min(CHUNK, amount - count))
                            if not data:
                                raise BackupError("来源文件大小在读取期间变化。")
                            dst.write(data)
                            digest.update(data)
                            piece_digest.update(data)
                            count += len(data)
                    segments.append({"part_number": len(parts) + 1, "member": name,
                                     "offset": total, "size": count, "sha256": piece_digest.hexdigest()})
                    directory_budget += 128 + len(name.encode("utf-8"))
                    total += count
                    if len(segments) > MAX_SEGMENTS:
                        raise BackupError("文件片段数量超过安全上限。")
                    if not amount:
                        break
                if source.read(1):
                    raise BackupError("来源文件大小在读取期间变化。")
            if before is not None:
                after = item["file"].stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ino):
                    raise BackupError("来源在读取期间变化，未将此次操作登记为成功。")
            manifest["files"].append({"path": normal_name, "size": total, "sha256": digest.hexdigest(),
                                      "mtime_ns": item["mtime_ns"]})
            mapping[normal_name] = segments
            if progress:
                progress(index + 1, len(items))
        finish_part()
        index = {"format": 3, "backup_id": manifest["backup_id"], "index_name": index_name,
                 "max_part_size": max_part_size, "manifest": manifest,
                 "parts": parts, "file_segments": mapping}
        payload = _json(index)
        if len(payload) > MAX_INDEX_SIZE:
            raise BackupError("整套备份总清单超过安全上限。")
        with Path(io_path(pending_index)).open("xb") as output:
            own(pending_index)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        with MultipartReader(pending_index, cancel, pending=True) as reader:
            checked = A._inspect_open(reader, cancel=cancel, work_dir=work_dir)
        A._cancel(cancel)
        # Windows rename is exclusive. POSIX link creates the final completion
        # marker atomically and never overwrites another file created meanwhile.
        if os.name == "nt":
            os.rename(io_path(pending_index), io_path(index_path))
        else:
            os.link(io_path(pending_index), io_path(index_path))
            Path(io_path(pending_index)).unlink()
        own(index_path)
        owned.pop(pending_index, None)
        files = [{"path": str(destination / x["name"]), "size": x["size"], "sha256": x["sha256"]} for x in parts]
        total_size = len(payload) + sum(x["size"] for x in parts)
        return {"ok": True, "path": files[0]["path"], "size": total_size, "total_size": total_size,
                "manifest": manifest, "warnings": [], "database_count": len(checked["database_checks"]),
                "archive_format": 3, "parts": files, "index_path": str(index_path),
                "index_size": len(payload), "index_sha256": hashlib.sha256(payload).hexdigest(),
                "part_count": len(parts)}
    except Exception:
        if z is not None:
            try:
                z.close()
            except Exception:
                pass  # Preserve the original failure and still clean owned artifacts.
        for path, identity in reversed(list(owned.items())):
            try:
                if Path(io_path(path)).exists() and _identity(path) == identity:
                    Path(io_path(path)).unlink()
            except OSError:
                pass  # Never touch a replacement file or any preceding recovery point.
        raise
