import pytest

from app.errors import ReviewError
from app.modules.intake.catalog import load_sample
from app.modules.intake.reader import read_source
from app.modules.runs.pipeline import calculate


@pytest.fixture(scope="module")
def legacy_package():
    return load_sample("legacy")


@pytest.fixture(scope="module")
def legacy_snapshot(legacy_package):
    return calculate(legacy_package)


def test_unknown_header_starts_quarantined_segment_and_keeps_raw_columns(legacy_package, legacy_snapshot):
    info = legacy_snapshot["files"][0]
    assert (info["segments"], info["recognized_segments"], info["unknown_segments"]) == (4, 3, 1)
    unknown = next(seg for seg in info["segments_detail"] if seg["status"] == "unknown")
    assert unknown["unknown_columns"] == ["tool_id", "col_x", "row_y", "probe_id", "grade", "score", "remark"]
    assert (unknown["start_line"], unknown["end_line"], unknown["data_rows"]) == (8, 11, 2)
    header_row = next(row for row in legacy_snapshot["rows"] if row["code"] == "UNKNOWN_HEADER")
    assert header_row["segment"] == unknown["index"] and header_row["bucket"] == "isolated"


def test_every_row_in_unknown_segment_is_isolated_never_candidate(legacy_snapshot):
    quarantined = [row for row in legacy_snapshot["rows"] if row["code"] == "UNKNOWN_SEGMENT"]
    assert {row["id"] for row in quarantined} == {"legacy-probe.csv:9", "legacy-probe.csv:10"}
    assert all(row["bucket"] == "isolated" and "fields" not in row for row in quarantined)
    # 未知段绝不进入对表、裁决与良率账
    assert all("transform" not in row and "measurement" not in row for row in quarantined)


def test_recognized_header_after_unknown_segment_resumes_table_matching(legacy_snapshot):
    detail = legacy_snapshot["files"][0]["segments_detail"]
    assert [seg["status"] for seg in detail] == ["recognized", "recognized", "unknown", "recognized"]
    assert detail[-1]["data_rows"] == 5
    decisions = legacy_snapshot["decisions"]["W-03"]
    assert decisions["0,0"]["result"] == "PASS" and decisions["-1,-1"]["result"] == "FAIL"
    # 隔离段的坐标不得产生任何有效测次（PENDING 占位不算采信）
    measured = {key: cell for key, cell in decisions.items() if cell["attempt"] is not None}
    assert "1,2" not in measured and "2,2" not in measured


def test_legacy_rows_still_conserve_five_buckets_and_declared_count(legacy_snapshot):
    audit = legacy_snapshot["audit"]["rows"]
    assert audit == {"total": 15, "declared_data": 12, "accepted": 7, "isolated": 5, "duplicate": 0,
                     "blank": 1, "header": 2, "balanced": True}
    assert {row["code"] for row in legacy_snapshot["rows"] if row["bucket"] == "isolated"} == {
        "UNKNOWN_HEADER", "UNKNOWN_SEGMENT", "COLUMN_COUNT"}


def test_legacy_intake_state_is_quarantined_with_count(legacy_snapshot):
    from app.modules.runs.service import intake_state
    assert intake_state(legacy_snapshot) == {"state": "QUARANTINED", "unknown_segments": 1}


def test_structural_header_check_does_not_misread_data_as_unknown_header(legacy_package):
    # 首档必须仍是可识别档头，否则整档按 BAD_HEADER 拒绝
    source = legacy_package.spec.sources[0]
    blob = legacy_package.files["legacy-probe.csv"]
    rows, info = read_source(source, blob)
    assert info["segments_detail"][0]["status"] == "recognized"
    # 含整数数值位的行即使文本奇怪也不视为档头
    assert all(row["code"] != "UNKNOWN_HEADER" for row in rows if row["line"] in (2, 3, 4))


def test_file_starting_with_unknown_header_is_rejected_not_silently_quarantined(legacy_package):
    source = legacy_package.spec.sources[0]
    blob = b"tool_id,col_x,row_y,probe_id,grade,score,remark\nW-03,1,2,1,PASS,1,x\n"
    with pytest.raises(ReviewError) as error:
        read_source(source, blob)
    assert error.value.code == "BAD_HEADER"


def test_unknown_segment_can_be_the_last_segment(legacy_package):
    source = legacy_package.spec.sources[0].model_copy(update={"records": 3})
    blob = ("wafer,x,y,attempt,result,bin,note\n"
            "W-03,0,4,1,PASS,1,a\n"
            "tool_id,col_x,row_y,probe_id,grade,score,remark\n"
            "W-03,1,2,1,PASS,1,b\n").encode()
    rows, info = read_source(source, blob)
    assert info["unknown_segments"] == 1 and info["recognized_segments"] == 1
    assert [seg["status"] for seg in info["segments_detail"]] == ["recognized", "unknown"]
    assert rows[-1]["code"] == "UNKNOWN_SEGMENT"
