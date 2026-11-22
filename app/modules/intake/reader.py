import csv
import io
import re
import unicodedata
from collections import Counter

from app.errors import ReviewError
from app.reasons import classify

HEADER = ("wafer", "x", "y", "attempt", "result", "bin", "note")
OLD_NAMES = {"片号": "wafer", "列": "x", "行": "y", "测次": "attempt",
             "判定": "result", "分档": "bin", "备注": "note"}
# 固定 7 栏契约下，档头行的 x/y/attempt/bin 四个数值位不会出现任何整数；
# 数据行这四个位至少有一个整数（全角数字先 NFKC）。以此识别"档头样行"，
# 不依赖栏名是否在已知别名表内，旧机台新栏名也不会冒充有效数据。
NUMERIC_SLOTS = (1, 2, 3, 5)
INTEGER = re.compile(r"[+-]?[0-9]{1,7}")


def decode(blob, name):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = blob.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            if encoding == "utf-8-sig" and not blob.startswith(b"\xef\xbb\xbf"):
                encoding = "utf-8"
            return text, encoding
        break
    raise ReviewError("BAD_ENCODING", f"{name} 编码无效")


def header(fields):
    normalized = [unicodedata.normalize("NFKC", value.lstrip("﻿")).strip().lower() for value in fields]
    return tuple(OLD_NAMES.get(value, value) for value in normalized)


def looks_like_header(fields):
    """按结构而非栏名判定档头样行：7 栏齐全、四个数值位没有任何整数。"""
    if len(fields) != 7 or not all(field.strip() for field in fields):
        return False
    for index in NUMERIC_SLOTS:
        if INTEGER.fullmatch(unicodedata.normalize("NFKC", fields[index]).strip()):
            return False
    return True


def open_segment(segments, columns, line, unknown, unknown_names=None):
    index = len(segments)
    segments.append({
        "index": index, "start_line": line, "end_line": line,
        "status": "unknown" if unknown else "recognized",
        "columns": list(columns) if columns is not None else [],
        "unknown_columns": unknown_names or [],
        "data_rows": 0,
    })
    return index


def read_source(source, blob):
    text, encoding = decode(blob, source.name)
    if not text:
        raise ReviewError("EMPTY_FILE", f"{source.name} 是空文件")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records = []
    counts = Counter()
    columns = None
    segments = []
    segment_index = None
    unknown_mode = False
    try:
        while True:
            line = reader.line_num + 1
            fields = next(reader, None)
            if fields is None:
                break
            empty = not any(field.strip() for field in fields)
            keys = () if empty else header(fields)
            is_known_header = not empty and len(keys) == len(HEADER) and set(keys) == set(HEADER)
            is_header_like = not empty and looks_like_header(fields)
            if not segments and not empty:
                if not is_known_header:
                    raise ReviewError("BAD_HEADER", f"{source.name}:{line} 档头无效")
                columns = keys
                segment_index = open_segment(segments, columns, line, unknown=False)
                unknown_mode = False
                counts["segments"] += 1
                counts["recognized_segments"] += 1
                continue
            row = {"id": f"{source.name}:{line}", "source": source.name, "line": line,
                   "end_line": reader.line_num, "raw": fields, "bucket": "candidate",
                   "code": None, "segment": segment_index}
            if segments:
                segments[segment_index]["end_line"] = reader.line_num
            if empty:
                classify(row, "blank", "BLANK_ROW")
            elif is_known_header:
                # 同档再次出现完整可识别档头：新开一段，后续数据按该段列映射。
                columns = keys
                segment_index = open_segment(segments, columns, line, unknown=False)
                unknown_mode = False
                row["segment"] = segment_index
                counts["segments"] += 1
                counts["recognized_segments"] += 1
                classify(row, "header", "REPEATED_HEADER")
            elif is_header_like:
                # 旧机台新栏名：隔离整段，直到再遇可识别档头才恢复对表。
                columns = None
                segment_index = open_segment(segments, keys, line, unknown=True, unknown_names=list(keys))
                unknown_mode = True
                row["segment"] = segment_index
                counts["segments"] += 1
                counts["unknown_segments"] += 1
                classify(row, "isolated", "UNKNOWN_HEADER")
            elif unknown_mode:
                # 未知段内的所有记录整段隔离，保留物理行与原始字段。
                classify(row, "isolated", "UNKNOWN_SEGMENT")
            elif len(fields) != len(columns):
                classify(row, "isolated", "COLUMN_COUNT")
            else:
                row["fields"] = dict(zip(columns, fields))
            if row["bucket"] != "blank" and row["code"] != "REPEATED_HEADER" and row["code"] != "UNKNOWN_HEADER":
                segments[segment_index]["data_rows"] += 1
            records.append(row)
            counts[row["bucket"]] += 1
    except csv.Error as exc:
        raise ReviewError("BROKEN_CSV", f"{source.name}:{reader.line_num} CSV 记录损坏") from exc

    if not segments:
        raise ReviewError("BLANK_FILE", f"{source.name} 只有空记录")
    data_count = len(records) - counts["header"] - counts["blank"]
    if data_count != source.records:
        raise ReviewError("RECORD_COUNT_MISMATCH", f"{source.name} 声明 {source.records} 行，实收 {data_count} 行")
    if data_count == 0:
        raise ReviewError("HEADER_ONLY", f"{source.name} 只有档头")
    return records, {"name": source.name, "encoding": encoding, "data_count": data_count,
                     "physical_lines": reader.line_num, "segments": counts["segments"],
                     "recognized_segments": counts["recognized_segments"],
                     "unknown_segments": counts["unknown_segments"],
                     "segments_detail": segments, "counts": dict(counts)}


def ingest(package):
    rows, files = [], []
    for source in package.spec.sources:
        records, stats = read_source(source, package.files[source.name])
        rows.extend(records)
        files.append(stats)
    return rows, files
