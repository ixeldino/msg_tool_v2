import argparse
import hashlib
import json
import logging
import struct
import sys
from pathlib import Path

import polib

ENCODING_CANDIDATES = ["utf-16-le", "cp1251", "cp866"]

# --- НАЛАШТУВАННЯ СИМВОЛУ ЗАПОВНЕННЯ ДЛЯ РЕЖИМУ ІН'ЄКЦІЇ ---
PADDING_CHAR = " "


def read_u32_le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_candidate(raw: bytes, encoding: str) -> tuple[str, float, float, float, float]:
    try:
        text = raw.decode(encoding, errors="replace")
    except Exception:
        return "", 0.0, 1.0, 0.0, 0.0
        
    if not text:
        return "", 0.0, 1.0, 0.0, 0.0
        
    total = len(text)
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t") / total
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t") / total
    cyrillic = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF") / total
    exotic = sum(1 for ch in text if (0x3000 <= ord(ch) <= 0x9FFF) or (0xAC00 <= ord(ch) <= 0xD7AF)) / total
    
    return text, printable, control, cyrillic, exotic


def choose_best_encoding(raw: bytes) -> tuple[str, str]:
    best = None
    best_score = float("-inf")
    
    for encoding in ENCODING_CANDIDATES:
        text, printable, control, cyrillic, exotic = decode_candidate(raw, encoding)
        score = printable - (control * 2.0) + (1.5 * cyrillic)
        
        if exotic > 0:
            score -= 50.0 * exotic  
            
        if encoding == "utf-16-le":
            null_ratio = raw.count(b"\x00") / max(1, len(raw))
            score += 0.2 * null_ratio
            
        if best is None or score > best_score:
            best = (encoding, text)
            best_score = score
            
    return best


def normalize_text(text: str) -> str:
    return text.rstrip("\x00")


def parse_msg_file(path: Path, exclude_path: Path | None = None) -> dict:
    data = path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"File too small to be a valid .msg: {path}")

    line_count = read_u32_le(data, 0)
    data_pool_size = read_u32_le(data, 4)
    system_flags = read_u32_le(data, 8)
    
    raw_data_offset = len(data) - data_pool_size
    base_address = 12 + (line_count * 4)
    separator_present = (raw_data_offset > base_address)
    separator_bytes = data[base_address:raw_data_offset] if separator_present else b""
    
    raw_data = data[raw_data_offset:]
    raw_offsets = [read_u32_le(data, 12 + i * 4) for i in range(line_count)]
    valid_offsets = [o for o in raw_offsets if 0 <= o < len(raw_data)]
    
    excluded_indices = set()
    if exclude_path and exclude_path.exists():
        try:
            exclude_data = json.loads(exclude_path.read_text(encoding="utf-8"))
            excluded_indices = set(int(x) for x in exclude_data.get("exclude_indices", []))
            print(f"[*] Завантажено {len(excluded_indices)} виключень. Ці блоки будуть захищені.")
        except Exception as e:
            print(f"[!] Помилка читання файлу виключень: {e}")
    
    all_unique_offsets = sorted(set(valid_offsets))
    master_starts = []
    current_end = 0
    
    for offset in all_unique_offsets:
        if offset >= current_end:
            master_starts.append(offset)
            chunk = raw_data[offset : offset + 2048]
            best_res = choose_best_encoding(chunk)
            encoding = best_res[0] if best_res else "utf-16-le"
            
            if encoding == "utf-16-le":
                clean_len = len(raw_data) - offset
                for i in range(0, len(raw_data) - offset - 1, 2):
                    if raw_data[offset+i : offset+i+2] == b"\x00\x00":
                        clean_len = i + 2
                        break
            else:
                idx = raw_data[offset:].find(b"\x00")
                clean_len = idx + 1 if idx != -1 else len(raw_data) - offset
            
            next_start = len(raw_data)
            for o in all_unique_offsets:
                if o >= offset + clean_len:
                    next_start = o
                    break
            current_end = next_start

    if not master_starts and valid_offsets:
        master_starts = [0]

    ranges = []
    for index, start in enumerate(master_starts):
        end = master_starts[index + 1] if index + 1 < len(master_starts) else len(raw_data)
        ranges.append((start, end))

    blocks = []
    content_map = {}
    content_items = []
    offset_to_block_id = {start: f"block_{start:06d}" for start in master_starts}

    for block_idx, (start, end) in enumerate(ranges):
        raw_bytes = raw_data[start:end]
        encoding, text = choose_best_encoding(raw_bytes)
        
        if encoding == "utf-16-le":
            for i in range(0, len(raw_bytes) - 1, 2):
                if raw_bytes[i:i+2] == b"\x00\x00":
                    raw_bytes = raw_bytes[:i+2]
                    break
        else:
            idx = raw_bytes.find(b"\x00")
            if idx != -1:
                raw_bytes = raw_bytes[:idx+1]

        text = normalize_text(raw_bytes.decode(encoding, errors="replace"))
        block_id = offset_to_block_id[start]
        raw_hex = raw_bytes.hex()
        raw_sha256 = sha256_hex(raw_bytes)
        is_excluded = block_idx in excluded_indices

        parts = text.split('\t')
        block_parts_info = []
        accumulated_text = ""

        for idx, part in enumerate(parts):
            if idx > 0:
                accumulated_text += "\t"
            if idx == 0:
                byte_off = 0
            else:
                byte_off = len(accumulated_text.encode(encoding, errors="ignore"))
            accumulated_text += part

            if part == "":
                block_parts_info.append({
                    "part_index": idx,
                    "is_empty": True,
                    "content_id": None,
                    "text": "",
                    "byte_offset": byte_off
                })
            else:
                if is_excluded:
                    block_parts_info.append({
                        "part_index": idx,
                        "is_empty": False,
                        "content_id": None,
                        "text": part,
                        "byte_offset": byte_off
                    })
                else:
                    if part not in content_map:
                        content_id = len(content_items)
                        content_map[part] = content_id
                        content_items.append({
                            "content_id": content_id,
                            "msgid": part,
                            "block_ids": [block_id],
                        })
                    else:
                        content_id = content_map[part]
                        if block_id not in content_items[content_id]["block_ids"]:
                            content_items[content_id]["block_ids"].append(block_id)

                    block_parts_info.append({
                        "part_index": idx,
                        "is_empty": False,
                        "content_id": content_id,
                        "text": part,
                        "byte_offset": byte_off
                    })

        blocks.append({
            "block_id": block_id,
            "raw_offset": start,
            "length": end - start,
            "raw_sha256": raw_sha256,
            "encoding": encoding,
            "raw_hex": raw_hex,
            "decoded_text": text,
            "translate": not is_excluded,
            "parts": block_parts_info
        })

    structure = []
    for index, raw_offset in enumerate(raw_offsets):
        if raw_offset < 0 or raw_offset >= len(raw_data):
            structure.append({
                "index": index,
                "type": "padding",
                "raw_offset": raw_offset,
            })
            continue

        if raw_offset in offset_to_block_id:
            structure.append({
                "index": index,
                "type": "regular",
                "raw_offset": raw_offset,
                "block_id": offset_to_block_id[raw_offset],
            })
            continue

        containing_start = None
        for start in reversed(master_starts):
            if start <= raw_offset:
                containing_start = start
                break

        if containing_start is not None:
            master_block_id = offset_to_block_id[containing_start]
            master_block_obj = next(b for b in blocks if b["block_id"] == master_block_id)
            
            if raw_offset < containing_start + master_block_obj["length"]:
                rel_sub_offset = raw_offset - containing_start
                target_part_index = 0
                intra_part_offset = rel_sub_offset

                for p in master_block_obj["parts"]:
                    if p["byte_offset"] <= rel_sub_offset:
                        target_part_index = p["part_index"]
                        intra_part_offset = rel_sub_offset - p["byte_offset"]

                structure.append({
                    "index": index,
                    "type": "virtual",
                    "raw_offset": raw_offset,
                    "master_block_id": master_block_id,
                    "target_part_index": target_part_index,
                    "intra_part_offset": intra_part_offset,
                })
                continue

        structure.append({
            "index": index,
            "type": "padding",
            "raw_offset": raw_offset,
        })

    segments = []
    cursor = 0
    for block in blocks:
        if block["raw_offset"] > cursor:
            gap_bytes = raw_data[cursor:block["raw_offset"]]
            segments.append({
                "type": "gap",
                "start": cursor,
                "end": block["raw_offset"],
                "raw_hex": gap_bytes.hex(),
            })
        segments.append({
            "type": "block",
            "block_id": block["block_id"],
            "raw_offset": block["raw_offset"],
            "length": block["length"],
            "encoding": block["encoding"],
            "raw_hex": block["raw_hex"],
        })
        cursor = block["raw_offset"] + block["length"]
    if cursor < len(raw_data):
        segments.append({
            "type": "gap",
            "start": cursor,
            "end": len(raw_data),
            "raw_hex": raw_data[cursor:].hex(),
        })

    manifest = {
        "source_file": path.name,
        "line_count": line_count,
        "data_pool_size": data_pool_size,
        "system_flags": system_flags,
        "base_address": base_address,
        "separator_present": separator_present,
        "separator_bytes": separator_bytes.hex() if separator_present else None,
        "raw_data_pool_size": len(raw_data),
        "raw_data_offset": raw_data_offset,
        "structure": structure,
        "blocks": blocks,
        "content_items": content_items,
        "segments": segments,
    }
    return manifest


def write_manifest(manifest: dict, path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def export_pot(manifest: dict, pot_path: Path) -> None:
    po = polib.POFile()
    po.metadata = {
        "Project-Id-Version": manifest.get("source_file", ""),
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
    }
    for item in manifest["content_items"]:
        msgid = item["msgid"]
        if len(msgid) < 2 and not msgid.isalnum():
            continue
            
        max_bytes = len(msgid.encode("utf-8"))
        if item["block_ids"]:
            first_block_id = item["block_ids"][0]
            block_obj = next((b for b in manifest["blocks"] if b["block_id"] == first_block_id), None)
            if block_obj:
                enc = block_obj["encoding"]
                if enc == "cp866":
                    enc = "cp1125"
                max_bytes = len(msgid.encode(enc, errors="replace"))

        entry = polib.POEntry(
            msgctxt=f"id_{item['content_id']}",
            msgid=msgid,
            msgstr="",
            comment=f"blocks: {', '.join(item['block_ids'])}",
        )
        entry.extracted_comment = f"max_bytes: {max_bytes}"
        po.append(entry)
    po.save(pot_path)


def load_po_translations(po_path: Path | None) -> dict[int, str]:
    if not po_path or not po_path.exists():
        return {}
    po = polib.pofile(str(po_path))
    translations = {}
    for entry in po:
        if not entry.msgctxt or not entry.msgctxt.startswith("id_"):
            continue
        try:
            content_id = int(entry.msgctxt[3:])
        except ValueError:
            continue
        if entry.msgstr and entry.msgstr.strip() != "":
            translations[content_id] = entry.msgstr
    return translations


def encode_block_text(text: str, encoding: str) -> bytes:
    if encoding == "cp866":
        encoding = "cp1125"
    encoded = text.encode(encoding, errors="replace")
    orig_term = b"\x00\x00" if encoding == "utf-16-le" else b"\x00"
    if not encoded.endswith(orig_term):
        encoded += orig_term
    return encoded


def adjust_text_by_bytes(text: str, max_bytes: int, encoding: str) -> bytes:
    enc_name = encoding if encoding != "cp866" else "cp1125"
    encoded = text.encode(enc_name, errors="replace")
    current_len = len(encoded)

    if current_len == max_bytes:
        return encoded

    if current_len > max_bytes:
        dot_encoded = "...".encode(enc_name, errors="replace")
        dot_len = len(dot_encoded)
        if max_bytes > 30 and (current_len - max_bytes) > 5 and max_bytes > dot_len:
            cutoff = max_bytes - dot_len
            if encoding == "utf-16-le" and (cutoff % 2 != 0):
                cutoff -= 1
            return encoded[:cutoff] + dot_encoded
        else:
            cutoff = max_bytes
            if encoding == "utf-16-le" and (cutoff % 2 != 0):
                cutoff -= 1
            return encoded[:cutoff]
    else:
        padding_needed = max_bytes - current_len
        pad_bytes = PADDING_CHAR.encode(enc_name, errors="replace")
        if len(pad_bytes) == 0:
            return encoded + (b"\x00" * padding_needed)
        if len(pad_bytes) == 1:
            return encoded + (pad_bytes * padding_needed)
        else:
            num_chars = padding_needed // len(pad_bytes)
            remainder = padding_needed % len(pad_bytes)
            return encoded + (pad_bytes * num_chars) + (b"\x00" * remainder)


def build_msg_from_manifest(manifest: dict, po_path: Path | None, output_path: Path, force: bool = False, align_all: int = 1, align_masters: int = 1, inject: bool = False) -> None:
    translations = load_po_translations(po_path) if po_path else {}
    segments = manifest["segments"]
    new_data_pool = bytearray()
    block_start = {}

    for segment in segments:
        if segment["type"] == "gap":
            new_data_pool.extend(bytes.fromhex(segment["raw_hex"]))
            continue

        block_id = segment["block_id"]
        block_obj = next(b for b in manifest["blocks"] if b["block_id"] == block_id)
        is_translatable = block_obj.get("translate", True)
        original_raw = bytes.fromhex(segment["raw_hex"])

        if inject:
            if not is_translatable:
                encoded = original_raw
            else:
                encoding = segment["encoding"]
                enc_name = encoding if encoding != "cp866" else "cp1125"
                tab_bytes = "\t".encode(enc_name, errors="replace")
                reassembled_bytes = []
                has_translation = False
                
                for part in block_obj["parts"]:
                    if part["is_empty"]:
                        reassembled_bytes.append(b"")
                    else:
                        cid = part["content_id"]
                        orig_part_encoded = part["text"].encode(enc_name, errors="replace")
                        max_bytes = len(orig_part_encoded)
                        
                        if cid is not None and int(cid) in translations:
                            has_translation = True
                            translated_text = translations[int(cid)]
                            adjusted_bytes = adjust_text_by_bytes(translated_text, max_bytes, encoding)
                        else:
                            adjusted_bytes = orig_part_encoded
                        reassembled_bytes.append(adjusted_bytes)
                
                if not has_translation:
                    encoded = original_raw
                else:
                    encoded = tab_bytes.join(reassembled_bytes)
                    orig_term = b"\x00\x00" if encoding == "utf-16-le" else b"\x00"
                    if not encoded.endswith(orig_term):
                        encoded += orig_term
                    
                    if len(encoded) < segment["length"]:
                        encoded += b"\x00" * (segment["length"] - len(encoded))
                    elif len(encoded) > segment["length"]:
                        encoded = encoded[:segment["length"]]
        else:
            reassembled_parts = []
            has_translation = False
            for part in block_obj["parts"]:
                if part["is_empty"]:
                    reassembled_parts.append("")
                else:
                    cid = part["content_id"]
                    if cid is not None and int(cid) in translations:
                        has_translation = True
                        translated_text = translations[int(cid)]
                    else:
                        translated_text = part["text"]
                    reassembled_parts.append(translated_text)

            text = "\t".join(reassembled_parts)
            if not is_translatable:
                encoded = original_raw
            elif not force and not has_translation:
                encoded = original_raw
            else:
                encoded = encode_block_text(text, segment["encoding"])
                alignment = max(align_all, align_masters) if segment["type"] == "block" else align_all
                if alignment > 1:
                    remainder = len(encoded) % alignment
                    if remainder != 0:
                        encoded += b"\x00" * (alignment - remainder)

        block_start[block_id] = len(new_data_pool)
        new_data_pool.extend(encoded)

    new_offsets = []
    for entry in manifest["structure"]:
        if inject:
            new_offsets.append(entry["raw_offset"])
        else:
            if entry["type"] in ("regular", "macro_master"):
                new_offsets.append(block_start[entry["block_id"]])
            elif entry["type"] == "virtual":
                master_start = block_start[entry["master_block_id"]]
                master_block = next(b for b in manifest["blocks"] if b["block_id"] == entry["master_block_id"])
                reassembled_prefix = []
                for p in master_block["parts"]:
                    if p["part_index"] < entry["target_part_index"]:
                        if p["is_empty"]:
                            reassembled_prefix.append("")
                        else:
                            cid = p["content_id"]
                            if cid is not None and int(cid) in translations:
                                translated_text = translations[int(cid)]
                            else:
                                translated_text = p["text"]
                            reassembled_prefix.append(translated_text)

                prefix_text = "\t".join(reassembled_prefix)
                if entry["target_part_index"] > 0:
                    prefix_text += "\t"

                new_rel_offset = len(prefix_text.encode(master_block["encoding"], errors="ignore")) + entry["intra_part_offset"]
                new_offsets.append(master_start + new_rel_offset)
            else:
                new_offsets.append(entry["raw_offset"])

    # --- ЗБИРАЄМО ГОЛОВНИЙ БУФЕР ЗАГОЛОВКА В ПАМ'ЯТІ ---
    header_bytes = bytearray()
    header_bytes.extend(struct.pack("<I", manifest["line_count"]))
    header_bytes.extend(struct.pack("<I", len(new_data_pool)))
    header_bytes.extend(struct.pack("<I", manifest["system_flags"]))
    for offset in new_offsets:
        header_bytes.extend(struct.pack("<I", offset))
    if manifest["separator_present"] and manifest["separator_bytes"]:
        header_bytes.extend(bytes.fromhex(manifest["separator_bytes"]))

    # --- ВИПРАВЛЕННЯ АНОМАЛІЇ НАКЛАДАННЯ (OVERLAP) ---
    # Якщо оригінальний пул даних заповзає на кінець таблиці офсетів, 
    # ми примусово обрізаємо заголовок до оригінального зміщення raw_data_offset.
    if "raw_data_offset" in manifest:
        orig_offset = manifest["raw_data_offset"]
        if len(header_bytes) > orig_offset:
            header_bytes = header_bytes[:orig_offset]

    with output_path.open("wb") as f:
        f.write(header_bytes)
        f.write(new_data_pool)


def validate_files(original_path: Path, rebuilt_path: Path, manifest_path: Path | None) -> int:
    orig = original_path.read_bytes()
    rebuilt = rebuilt_path.read_bytes()
    
    print(f"Розмір оригіналу: {len(orig)} байт")
    print(f"Розмір ребілду:   {len(rebuilt)} байт")
    
    orig_lines = struct.unpack_from("<I", orig, 0)[0] if len(orig) >= 4 else 0
    rebuilt_lines = struct.unpack_from("<I", rebuilt, 0)[0] if len(rebuilt) >= 4 else 0
    print(f"Кількість рядків: Оригінал={orig_lines}, Ребілд={rebuilt_lines}")
    
    min_len = min(len(orig), len(rebuilt))
    mismatch_idx = None
    for i in range(min_len):
        if orig[i] != rebuilt[i]:
            mismatch_idx = i
            break
            
    if mismatch_idx is None:
        if len(orig) == len(rebuilt):
            print("\n[+] ВАЛІДАЦІЯ УСПІШНА: Файли абсолютно побітово ідентичні!")
            return 0
        else:
            mismatch_idx = min_len
            print("\n[!] ПОПЕРЕДЖЕННЯ: Розбіжність виявлена в кінці файлу (різна довжина пулу).")
            
    print(f"\n[!] Перша розбіжність знайдена на байті: {mismatch_idx} (Hex: 0x{mismatch_idx:X})")
    
    manifest = None
    if manifest_path and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    if manifest:
        raw_data_offset = manifest.get("raw_data_offset", 8)
        pool_mismatch_idx = mismatch_idx - raw_data_offset
        print(f"Позиція розбіжності всередині DataPool: {pool_mismatch_idx} (Hex: 0x{pool_mismatch_idx:X})")
        
        target_block = None
        for block in manifest.get("blocks", []):
            start = block["raw_offset"]
            end = start + block["length"]
            if start <= pool_mismatch_idx < end:
                target_block = block
                break
                
        if target_block:
            print(f"\n=== АНАЛІЗ ПРОБЛЕМНОГО БЛОКУ ===")
            print(f"ID Блоку:      {target_block['block_id']}")
            print(f"Офсет блоку:   {target_block['raw_offset']}")
            print(f"Ориг. довжина: {target_block['length']} байт")
            print(f"Кодування:     {target_block['encoding']}")
            print(f"Текст блоку:   {repr(target_block['decoded_text'])}")
        else:
            print("\n[i] Розбіжність лежить поза межами відомих текстових блоків.")
            
    context_start = max(0, mismatch_idx - 16)
    context_end = min(len(orig), mismatch_idx + 32)
    print(f"\nОриг. дамп:    {orig[context_start:context_end].hex(' ').upper()}")
    context_rebuilt_end = min(len(rebuilt), mismatch_idx + 32)
    print(f"Ребілд дамп:   {rebuilt[context_start:context_rebuilt_end].hex(' ').upper()}")
    return 1


def inspect_msg(path: Path) -> int:
    manifest = parse_msg_file(path)
    types = {"regular": 0, "macro_master": 0, "virtual": 0, "padding": 0}
    for entry in manifest["structure"]:
        types[entry["type"]] = types.get(entry["type"], 0) + 1
    encodings = {}
    for block in manifest["blocks"]:
        encodings[block["encoding"]] = encodings.get(block["encoding"], 0) + 1
        
    print(f"Source: {manifest['source_file']}")
    print(f"Line count: {manifest['line_count']}")
    print(f"Structure counts: {types}")
    print(f"Physical block count: {len(manifest['blocks'])}")
    print(f"Detected encodings: {encodings}")
    return 0


def run_extract(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: Input file '{input_path}' does not exist.")
        return 1

    exclude_path = Path(args.exclude) if args.exclude else None
    manifest = parse_msg_file(input_path, exclude_path=exclude_path)
    manifest_path = Path(args.manifest) if args.manifest else input_path.with_suffix(".manifest")
    pot_path = Path(args.pot) if args.pot else input_path.with_suffix(".pot")
    
    write_manifest(manifest, manifest_path)
    export_pot(manifest, pot_path)
    print(f"Manifest written to {manifest_path}\nPOT template written to {pot_path}")
    return 0


def run_build(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"Error: Manifest file '{manifest_path}' does not exist.")
        return 1

    if args.po:
        po_path = Path(args.po)
        if not po_path.is_file():
            print(f"Error: PO translation file '{po_path}' does not exist.")
            return 1
    else:
        po_path = None

    output_path = Path(args.output) if args.output else manifest_path.with_suffix("_rebuilt.msg")
    manifest = load_manifest(manifest_path)
    build_msg_from_manifest(
        manifest, 
        po_path, 
        output_path, 
        force=args.force,
        align_all=args.align_all,
        align_masters=args.align_masters,
        inject=args.inject
    )
    print(f"Built file written to {output_path}")
    return 0


def run_validate(args: argparse.Namespace) -> int:
    original_path = Path(args.original)
    if not original_path.is_file():
        print(f"Error: Original file '{original_path}' does not exist.")
        return 1

    rebuilt_path = Path(args.rebuilt)
    if not rebuilt_path.is_file():
        print(f"Error: Rebuilt file '{rebuilt_path}' does not exist.")
        return 1

    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            print(f"Error: Manifest file '{manifest_path}' does not exist.")
            return 1
    else:
        manifest_path = original_path.with_suffix(".manifest")

    return validate_files(original_path, rebuilt_path, manifest_path)


def run_validate(args: argparse.Namespace) -> int:
    original_path = Path(args.original)
    if not original_path.is_file():
        print(f"Error: Original file '{original_path}' does not exist.")
        return 1

    rebuilt_path = Path(args.rebuilt)
    if not rebuilt_path.is_file():
        print(f"Error: Rebuilt file '{rebuilt_path}' does not exist.")
        return 1

    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            print(f"Error: Manifest file '{manifest_path}' does not exist.")
            return 1
    else:
        manifest_path = original_path.with_suffix(".manifest")

    return validate_files(original_path, rebuilt_path, manifest_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Acronis .MSG Localization Tool Suite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- СУБКОМАНДА: EXTRACT ---
    p_extract = subparsers.add_add_parser("extract", help="Розбір бінарного файлу .msg")
    p_extract.add_argument("input", help="Шлях до оригінального файлу .msg")
    p_extract.add_argument("--manifest", help="Шлях для збереження JSON-маніфесту")
    p_extract.add_argument("--pot", help="Шлях для збереження шаблону .pot")
    p_extract.add_argument("--exclude", help="Шлях до файлу виключень exclude_list.json")

    # --- СУБКОМАНДА: BUILD ---
    p_build = subparsers.add_parser("build", help="Збірка локалізованого .msg")
    p_build.add_argument("--manifest", required=True, help="Шлях до JSON-маніфесту структури")
    p_build.add_argument("--po", help="Шлях до файлу перекладу .po")
    p_build.add_argument("--output", help="Шлях для збереження вихідного файлу .msg")
    p_build.add_argument("--force", action="store_true", help="Примусова компіляція неперекладених блоків")
    p_build.add_argument("--align-all", type=int, default=1, help="Загальне вирівнювання байтів (наприклад, 1 або 4)")
    p_build.add_argument("--align-masters", type=int, default=1, help="Вирівнювання для великих master-блоків")
    p_build.add_argument("--inject", action="store_true", help="Режим суворої побітової ін'єкції (Byte-for-Byte)")

    # --- СУБКОМАНДА: VALIDATE ---
    p_validate = subparsers.add_parser("validate", help="Побітове порівняння двох файлів")
    p_validate.add_argument("--original", required=True, help="Шлях до оригінального .msg")
    p_validate.add_argument("--rebuilt", required=True, help="Шлях до зібраного .msg")
    p_validate.add_argument("--manifest", help="Шлях до маніфесту для глибокого аналізу")

    # --- СУБКОМАНДА: INSPECT ---
    p_inspect = subparsers.add_parser("inspect", help="Швидка інспекція метаданих файлу")
    p_inspect.add_argument("input", help="Шлях до файлу .msg")

    args = parser.parse_args()

    if args.command == "extract":
        sys.exit(run_extract(args))
    elif args.command == "build":
        sys.exit(run_build(args))
    elif args.command == "validate":
        sys.exit(run_validate(args))
    elif args.command == "inspect":
        sys.exit(run_validate(args) if hasattr(args, "original") else inspect_msg(Path(args.input)))


if __name__ == "__main__":
    main()
