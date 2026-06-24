"""文件后缀名混淆与恢复工具（防和谐功能）。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import string
import sys
from pathlib import Path
from typing import Callable, Iterable

from bpc_cli.common import (
    ARCHIVE_EXTENSIONS,
    _split_archive_ext,
    _split_volume,
    get_volume_group,
    looks_like_archive_or_volume,
)
from bpc_cli.file_type import detect_archive_type

MAP_FILENAME = ".bpc_extmap"
SUFFIX_LENGTH = 4
ALPHABET = string.ascii_letters + string.digits
SALT_LEN = 16
NONCE_LEN = 16
TAG_LEN = 16
BLOCK_LEN = 32
ITERATIONS = 100_000


class ObfuscateError(Exception):
    """后缀名混淆/恢复异常。"""


class MapCryptoError(Exception):
    """映射文件加解密异常。"""


def generate_suffix(length: int = SUFFIX_LENGTH) -> str:
    """使用加密安全随机算法生成指定长度的字母数字后缀。"""
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def generate_random_obfuscated_ext(length: int = SUFFIX_LENGTH) -> str:
    """生成完全随机的非标准文件扩展名（含前导点）。"""
    standard = {e.lower() for e in ARCHIVE_EXTENSIONS}
    for _ in range(max_retries := 100):
        ext = "." + "".join(secrets.choice(ALPHABET) for _ in range(length))
        if ext.lower() not in standard:
            return ext
    raise ObfuscateError("无法生成非标准扩展名，请检查长度或扩展名列表")


def _derive_key(password: str, salt: bytes) -> bytes:
    """基于 PBKDF2-HMAC-SHA256 派生 256-bit 密钥。"""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=32
    )


def _hmac_digest(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """HMAC-CTR 风格的密钥流异或加密。"""
    out = bytearray()
    for i in range(0, len(data), BLOCK_LEN):
        block = data[i : i + BLOCK_LEN]
        counter = i.to_bytes(4, byteorder="big", signed=False)
        keystream = _hmac_digest(key, nonce + counter)
        out.extend(b ^ k for b, k in zip(block, keystream))
    return bytes(out)


def encrypt_mapping(data: bytes, password: str | None = None) -> bytes:
    """加密映射数据并附加认证标签。"""
    salt = secrets.token_bytes(SALT_LEN)
    nonce = secrets.token_bytes(NONCE_LEN)
    key = _derive_key(password or "", salt)
    ciphertext = _xor_stream(data, key, nonce)
    tag = _hmac_digest(key, salt + nonce + ciphertext)[:TAG_LEN]
    return salt + nonce + ciphertext + tag


def decrypt_mapping(token: bytes, password: str | None = None) -> bytes:
    """解密并校验映射数据。"""
    min_len = SALT_LEN + NONCE_LEN + TAG_LEN
    if len(token) < min_len:
        raise MapCryptoError("映射文件长度异常")

    salt = token[:SALT_LEN]
    nonce = token[SALT_LEN : SALT_LEN + NONCE_LEN]
    ciphertext = token[SALT_LEN + NONCE_LEN : -TAG_LEN]
    tag = token[-TAG_LEN:]

    key = _derive_key(password or "", salt)
    expected_tag = _hmac_digest(key, salt + nonce + ciphertext)[:TAG_LEN]
    if not hmac.compare_digest(tag, expected_tag):
        raise MapCryptoError("映射文件解密失败：密码错误或数据损坏")

    return _xor_stream(ciphertext, key, nonce)


def _set_hidden(path: Path) -> None:
    """在 Windows 上设置文件隐藏属性；Unix 下点号开头即隐藏。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
        except Exception:
            pass


def _random_archive_ext(original_ext: str) -> str:
    """随机选择一个与原始扩展名不同的压缩包扩展名。"""
    candidates = [e for e in ARCHIVE_EXTENSIONS if e.lower() != original_ext.lower()]
    if not candidates:
        candidates = list(ARCHIVE_EXTENSIONS)
    return secrets.choice(candidates)


def _split_suffix(name: str) -> tuple[str, str] | None:
    """
    返回 (stem, extension)，其中 extension 包含前导点。
    对无后缀、以点开头的隐藏文件返回 None。
    """
    if name.startswith(".") or "." not in name:
        return None
    stem, _, ext = name.rpartition(".")
    if not ext:
        return None
    return stem, "." + ext


def _match_obfuscated_name(
    name: str, mapping: dict[str, str] | dict[str, dict[str, str]]
) -> tuple[str, str, str, str] | None:
    """
    判断文件名是否为混淆后的压缩包/分卷。

    返回 (stem, token, new_archive_ext, volume_suffix)。
    volume_suffix 为空字符串时表示单文件压缩包。

    兼容 v2（mapping[token] 为原始扩展名字符串）与 v3（mapping[token]
    为 {"original_ext": ..., "obfuscated_ext": ...}）。
    """
    for token, value in mapping.items():
        if isinstance(value, dict):
            ext = value["obfuscated_ext"]
            ext_no_dot = ext[1:]
            # 分卷：stem.token.obfuscated_ext.001
            vol_match = re.match(
                rf"^(.*)\.{re.escape(token)}\.{re.escape(ext_no_dot)}(\.\d{{3,}})$",
                name,
                re.IGNORECASE,
            )
            if vol_match:
                return vol_match.group(1), token, ext, vol_match.group(2)
            # 单文件：stem.token.obfuscated_ext
            single_match = re.match(
                rf"^(.*)\.{re.escape(token)}\.{re.escape(ext_no_dot)}$",
                name,
                re.IGNORECASE,
            )
            if single_match:
                return single_match.group(1), token, ext, ""
        else:
            # v2 兼容路径：遍历所有已知压缩扩展名
            for ext in ARCHIVE_EXTENSIONS:
                ext_no_dot = ext[1:]
                # 分卷：stem.token.ext.001
                vol_match = re.match(
                    rf"^(.*)\.{re.escape(token)}\.{re.escape(ext_no_dot)}(\.\d{{3,}})$",
                    name,
                    re.IGNORECASE,
                )
                if vol_match:
                    return vol_match.group(1), token, ext, vol_match.group(2)
                # 单文件：stem.token.ext
                single_match = re.match(
                    rf"^(.*)\.{re.escape(token)}\.{re.escape(ext_no_dot)}$",
                    name,
                    re.IGNORECASE,
                )
                if single_match:
                    return single_match.group(1), token, ext, ""
    return None


def obfuscate_directory(
    root: Path,
    password: str | None = None,
    max_retries: int = 5,
    verbose: bool = False,
    only_archives: bool = True,
    custom_ext_factory: Callable[[str], str] | None = None,
) -> dict[str, dict[str, str]]:
    """
    将 root 目录下压缩包/分卷文件的后缀名进行混淆，并加密保存映射文件。

    默认仅处理压缩包（only_archives=True）。可通过 custom_ext_factory 指定
    自定义混淆后缀生成器；传入时生成完全随机后缀，否则从已知合法压缩扩展
    名中随机选择。

    返回 token -> {"original_ext": ..., "obfuscated_ext": ...} 的映射字典
    （v3 格式）。
    """
    mapping: dict[str, dict[str, str]] = {}
    used_tokens: set[str] = set()
    # 同一组分卷复用同一个 token 与新扩展名
    volume_groups: dict[tuple[str, str, Path], tuple[str, str]] = {}

    ext_factory = custom_ext_factory if custom_ext_factory is not None else _random_archive_ext

    files = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: str(p),
        reverse=True,
    )

    for path in files:
        if path.name == MAP_FILENAME or path.name.startswith(MAP_FILENAME):
            continue

        if only_archives:
            if not looks_like_archive_or_volume(path):
                if verbose:
                    print(f"跳过非压缩包文件: {path}")
                continue

            vol_split = _split_volume(path.name)
            if vol_split is not None:
                stem, original_ext, vol_suffix = vol_split
                group_key = (stem, original_ext, path.parent)
                if group_key not in volume_groups:
                    token = generate_suffix()
                    while token in used_tokens:
                        token = generate_suffix()
                    used_tokens.add(token)
                    new_archive_ext = ext_factory(original_ext)
                    volume_groups[group_key] = (token, new_archive_ext)
                token, new_archive_ext = volume_groups[group_key]
            else:
                single_split = _split_archive_ext(path.name)
                if single_split is None:
                    continue
                stem, original_ext = single_split
                vol_suffix = ""
                token = generate_suffix()
                while token in used_tokens:
                    token = generate_suffix()
                used_tokens.add(token)
                new_archive_ext = ext_factory(original_ext)
        else:
            # 兼容旧行为：混淆所有普通文件后缀
            split = _split_suffix(path.name)
            if split is None:
                if verbose:
                    print(f"跳过无后缀或隐藏文件: {path}")
                continue
            stem, original_ext = split
            vol_suffix = ""
            token = generate_suffix()
            while token in used_tokens:
                token = generate_suffix()
            used_tokens.add(token)
            new_archive_ext = ""

        if only_archives:
            new_name = f"{stem}.{token}{new_archive_ext}{vol_suffix}"
        else:
            new_name = f"{stem}.{token}{vol_suffix}"

        new_path = path.with_name(new_name)
        if new_path.exists() and new_path != path:
            raise ObfuscateError(f"混淆目标文件名已存在: {new_path}")

        try:
            path.rename(new_path)
        except OSError as exc:
            raise ObfuscateError(
                f"重命名文件失败: {path} -> {new_path}: {exc}"
            ) from exc

        mapping[token] = {"original_ext": original_ext, "obfuscated_ext": new_archive_ext}

    map_payload = json.dumps(
        {"version": 3, "mapping": mapping}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    encrypted = encrypt_mapping(map_payload, password)
    map_path = root / MAP_FILENAME
    map_path.write_bytes(encrypted)
    _set_hidden(map_path)

    return mapping


def restore_directory(
    root: Path,
    password: str | None = None,
    max_retries: int = 3,
    verbose: bool = False,
) -> dict[Path, Path]:
    """
    从 root 目录中读取加密映射文件并恢复原始后缀名。

    返回 {原混淆路径: 恢复后路径}。
    """
    map_path: Path | None = None
    for candidate in root.rglob(MAP_FILENAME):
        if candidate.is_file():
            map_path = candidate
            break

    if map_path is None:
        return {}

    encrypted = map_path.read_bytes()
    try:
        payload = decrypt_mapping(encrypted, password)
    except MapCryptoError as exc:
        raise ObfuscateError(f"无法解密后缀映射文件: {exc}") from exc

    data = json.loads(payload.decode("utf-8"))
    mapping = data.get("mapping", {})

    restored: dict[Path, Path] = {}
    files = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: str(p),
        reverse=True,
    )

    for path in files:
        if path.name == MAP_FILENAME:
            continue

        match = _match_obfuscated_name(path.name, mapping)
        if match is None:
            continue
        stem, token, _new_archive_ext, vol_suffix = match
        entry = mapping[token]
        original_ext = entry["original_ext"] if isinstance(entry, dict) else entry
        new_name = f"{stem}{original_ext}{vol_suffix}"
        new_path = path.with_name(new_name)

        if new_path.exists() and new_path != path:
            for attempt in range(1, max_retries + 1):
                alt = new_path.with_name(
                    f"{new_path.stem}_conflict{attempt}{original_ext}{vol_suffix}"
                )
                if not alt.exists():
                    new_path = alt
                    break
            else:
                raise ObfuscateError(f"恢复目标文件已存在且无法解决冲突: {new_path}")

        try:
            path.rename(new_path)
            restored[path] = new_path
        except OSError as exc:
            raise ObfuscateError(f"恢复文件失败: {path} -> {new_path}: {exc}") from exc

    try:
        map_path.unlink()
    except OSError as exc:
        if verbose:
            print(f"警告：无法删除映射文件 {map_path}: {exc}")

    return restored


def _final_archive_ext(name: str) -> str | None:
    """返回最终压缩包文件名中可被识别的压缩扩展名；混淆后应返回 None。"""
    vol = _split_volume(name)
    if vol is not None:
        return vol[1]
    single = _split_archive_ext(name)
    return single[1] if single else None


def obfuscate_final_archive(path: Path) -> list[Path]:
    """对最终输出的压缩包（单文件或分卷组）进行后缀名混淆。

    仅修改文件名，不修改文件内容。分卷组会共享同一个混淆后缀。
    返回所有混淆后的文件路径列表，首元素为单文件或分卷组的首卷。
    """
    if not path.exists():
        raise ObfuscateError(f"待混淆文件不存在: {path}")

    new_ext = generate_random_obfuscated_ext()
    new_ext_no_dot = new_ext[1:]

    vol_split = _split_volume(path.name)
    if vol_split is not None:
        stem, _original_ext, _vol_suffix = vol_split
        volumes = get_volume_group(path)
        new_paths: list[Path] = []
        for vol in volumes:
            vol_split2 = _split_volume(vol.name)
            if vol_split2 is None:
                continue
            _, _, vol_suffix = vol_split2
            new_name = f"{stem}.{new_ext_no_dot}{vol_suffix}"
            new_path = vol.with_name(new_name)
            if new_path.exists() and new_path != vol:
                raise ObfuscateError(f"混淆目标文件名已存在: {new_path}")
            vol.rename(new_path)
            new_paths.append(new_path)
        if not new_paths:
            return [path]
        return new_paths

    single_split = _split_archive_ext(path.name)
    if single_split is not None:
        stem, _original_ext = single_split
    else:
        stem = path.stem

    new_name = f"{stem}.{new_ext_no_dot}"
    new_path = path.with_name(new_name)
    if new_path.exists() and new_path != path:
        raise ObfuscateError(f"混淆目标文件名已存在: {new_path}")
    path.rename(new_path)
    return [new_path]


def verify_final_obfuscation(obfuscated: Path) -> None:
    """验证最终压缩包后缀混淆结果。

    确认：
    - 混淆后的文件存在；
    - 新后缀不是任何已知的标准压缩扩展名（扩展名检测失效）；
    - 文件内容仍能被 magic bytes 识别为压缩包（内容未损坏）。
    """
    if not obfuscated.exists():
        raise ObfuscateError(f"混淆后文件不存在: {obfuscated}")

    current_ext = _final_archive_ext(obfuscated.name)
    if current_ext is not None and current_ext.lower() in {e.lower() for e in ARCHIVE_EXTENSIONS}:
        raise ObfuscateError(
            f"混淆后缀仍为已知压缩扩展名: {current_ext}"
        )

    if detect_archive_type(obfuscated) is None:
        raise ObfuscateError(
            "混淆后文件头无法识别为已知压缩格式，内容可能已损坏"
        )


def list_obfuscated_files(root: Path) -> Iterable[Path]:
    """列出 root 下当前处于混淆状态的压缩包/分卷文件（用于测试/校验）。"""
    # 优先读取映射文件，支持 v3 完全随机后缀
    tokens: set[str] = set()
    for candidate in root.rglob(MAP_FILENAME):
        if not candidate.is_file():
            continue
        try:
            payload = decrypt_mapping(candidate.read_bytes())
            data = json.loads(payload.decode("utf-8"))
            tokens.update(data.get("mapping", {}).keys())
        except Exception:
            continue

    for path in root.rglob("*"):
        if not path.is_file() or path.name == MAP_FILENAME:
            continue
        if looks_like_archive_or_volume(path):
            yield path
            continue
        # 随机后缀文件：文件名包含任一 token 即视为混淆后的压缩包
        if any(f".{token}." in path.name or path.name.endswith(f".{token}") for token in tokens):
            yield path
