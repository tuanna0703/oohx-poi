"""Dashboard — how much data the lake holds and whether the pipeline is moving.

Written for someone who has not memorised the schema: every number says what
it counts, the pipeline is shown in the order data flows through it, and each
health check states its verdict in words (icon + label, never colour alone).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from admin.lib.llm import breaker_banner
from admin.lib.queries import (
    category_breakdown,
    crawl_progress,
    daily_growth,
    jobs_status_breakdown,
    overview_totals,
    per_source_stats,
    province_coverage,
)

st.set_page_config(page_title="Tổng quan — POI Lake", layout="wide")

BAR = "#2a78d6"  # one series per chart, so one hue


def n(v: object) -> str:
    return f"{int(v or 0):,}".replace(",", ".")


def pct(part: object, whole: object) -> str:
    whole_i = int(whole or 0)
    return f"{100 * int(part or 0) / whole_i:.1f}%" if whole_i else "—"


def ago(ts: object) -> str:
    if ts is None or pd.isna(ts):
        return "chưa bao giờ"
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(UTC)
    s = (datetime.now(UTC) - t.to_pydatetime()).total_seconds()
    if s < 90:
        return "vài giây trước"
    if s < 3600:
        return f"{int(s // 60)} phút trước"
    if s < 86400:
        return f"{int(s // 3600)} giờ trước"
    return f"{int(s // 86400)} ngày trước"


def age_s(ts: object) -> float:
    if ts is None or pd.isna(ts):
        return float("inf")
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(UTC)
    return (datetime.now(UTC) - t.to_pydatetime()).total_seconds()


OK, WARN, BAD = "🟢 Ổn", "🟠 Cần xem", "🔴 Có vấn đề"

# ---------------------------------------------------------------- header

head, btn = st.columns([5, 1])
head.title("Tổng quan POI Lake")
head.caption(
    "Kho dữ liệu địa điểm (POI) cho OOHX: crawl từ Google Maps → làm sạch → "
    "gộp trùng → phục vụ Data Engine qua API. Số liệu cập nhật mỗi phút."
)
if btn.button("Làm mới", width="stretch"):
    st.cache_data.clear()
    st.rerun()

llm_paused = breaker_banner()

t = overview_totals()
c = crawl_progress()

# ---------------------------------------------------------------- headline

st.subheader("Hệ thống đang có bao nhiêu dữ liệu?")
k = st.columns(4)
k[0].metric(
    "Địa điểm sạch (master POI)", n(t["masters"]),
    delta=f"+{n(t['masters_24h'])} được gộp xong trong 24 giờ", delta_color="off",
    help="Mỗi master là một địa điểm thật, đã gộp các bản ghi trùng. "
         "Đây là con số Data Engine và các hệ thống khác dùng. "
         "Số 'mới' tính theo lúc gộp trùng xong, không phải lúc crawl — khi "
         "dedupe xử lý tồn đọng, nó có thể lớn hơn nhiều so với lượng crawl.",
)
k[1].metric(
    "Bản ghi thô đã crawl", n(t["raw"]),
    delta=f"+{n(t['raw_24h'])} trong 24 giờ", delta_color="off",
    help="Mọi kết quả lấy về từ nguồn (chủ yếu Google Maps qua gosom), kể cả trùng. "
         "Một địa điểm crawl nhiều lần sẽ có nhiều bản ghi thô.",
)
k[2].metric(
    "Tỉnh/thành có dữ liệu", f"{n(t['provinces'])} / 63",
    help="Số tỉnh có ít nhất một master POI.",
)
k[3].metric(
    "Độ phủ crawl", pct(c["cells_done"], c["cells_total"]),
    delta=f"{n(c['cells_done'])} / {n(c['cells_total'])} ô lưới", delta_color="off",
    help="Mỗi tỉnh × danh mục được chia thành ô 5 km; mỗi ô là một lượt crawl.",
)

# ---------------------------------------------------------------- pipeline

st.subheader("Dữ liệu đi qua hệ thống như thế nào?")
st.caption("Từ trái sang phải là thứ tự xử lý. Số trong ngoặc là phần còn đang chờ ở bước đó.")
p = st.columns(4)
with p[0]:
    st.markdown("**① Crawl**")
    st.markdown(f"### {n(c['jobs_24h'])}")
    st.caption(
        f"job crawl xong trong 24 giờ · {n(t['jobs_inflight'])} đang chạy. "
        f"Lấy về {n(c['fetched_24h'])} kết quả, {n(c['new_24h'])} là mới."
    )
with p[1]:
    st.markdown("**② Bản ghi thô (bronze)**")
    st.markdown(f"### {n(t['raw'])}")
    st.caption(f"Lưu nguyên văn từ nguồn. ({n(t['raw_unprocessed'])} chưa làm sạch)")
with p[2]:
    st.markdown("**③ Đã làm sạch (silver)**")
    st.markdown(f"### {n(t['processed'])}")
    st.caption(
        "Chuẩn hoá tên, địa chỉ, SĐT, danh mục, thương hiệu. "
        f"({n(t['pending'])} chờ gộp trùng)"
    )
with p[3]:
    st.markdown("**④ Địa điểm sạch (gold)**")
    st.markdown(f"### {n(t['masters'])}")
    st.caption(
        f"Sau khi gộp trùng. {n(t['merged_away'])} master cũ đã được gộp vào "
        "master khác. Đây là dữ liệu API trả ra."
    )

# ---------------------------------------------------------------- health

st.subheader("Hệ thống có đang chạy bình thường không?")

checks: list[tuple[str, str, str]] = []

if c["rows_active"] == 0 and c["rows_paused"] > 0:
    checks.append(("Crawl", BAD,
                   f"Toàn bộ {n(c['rows_paused'])} hàng kế hoạch crawl đang tạm dừng. "
                   "Bấm Resume ở trang Crawl Coverage nếu đã xử lý xong nguyên nhân."))
elif age_s(t["last_job_at"]) > 1800 and c["rows_active"] > 0:
    checks.append(("Crawl", WARN,
                   f"Không có job nào xong từ {ago(t['last_job_at'])}, "
                   f"dù còn {n(c['rows_active'])} hàng kế hoạch chờ crawl."))
else:
    checks.append(("Crawl", OK,
                   f"Job gần nhất xong {ago(t['last_job_at'])}; "
                   f"{n(c['jobs_24h'])} job trong 24 giờ."))

if c["jobs_24h"]:
    empty_rate = c["jobs_empty_24h"] / c["jobs_24h"]
    failed = c["jobs_failed_24h"]
    verdict = WARN if empty_rate > 0.8 or failed > c["jobs_24h"] * 0.1 else OK
    checks.append(("Hiệu suất crawl", verdict,
                   f"{empty_rate:.0%} job không ra kết quả nào; "
                   f"trung bình {c['fetched_24h'] / c['jobs_24h']:.1f} kết quả/job; "
                   f"{n(failed)} job lỗi."))

if c["cells_known"]:
    pending_cells = c["cells_known"] - c["cells_populated"] - c["cells_empty"]
    checks.append(("Thăm dò ô", OK,
                   f"{n(c['cells_known'])} ô đã lập: {n(c['cells_populated'])} có dữ liệu, "
                   f"{n(c['cells_empty'])} trống, {n(pending_cells)} chưa thăm dò. "
                   f"{n(c['probes_24h'])} lượt thăm dò trong 24 giờ; "
                   f"đã bỏ qua {n(c['cells_skipped'])} lượt crawl vào ô trống."))

if llm_paused:
    checks.append(("Gộp trùng (dedupe)", BAD, "Đang tạm dừng vì Anthropic API từ chối — xem banner phía trên."))
elif t["pending"] > 50_000:
    checks.append(("Gộp trùng (dedupe)", WARN,
                   f"{n(t['pending'])} bản ghi chờ gộp — tồn đọng lớn."))
elif t["pending"] > 0 and age_s(t["last_master_at"]) > 3600:
    checks.append(("Gộp trùng (dedupe)", WARN,
                   f"{n(t['pending'])} bản ghi chờ nhưng không có master mới từ "
                   f"{ago(t['last_master_at'])}."))
else:
    checks.append(("Gộp trùng (dedupe)", OK,
                   f"{n(t['pending'])} bản ghi chờ gộp; master mới nhất tạo "
                   f"{ago(t['last_master_at'])}."))

raw_age = age_s(t["last_raw_at"])
checks.append(("Dữ liệu mới", OK if raw_age < 3600 else WARN if raw_age < 86400 else BAD,
               f"Bản ghi thô mới nhất: {ago(t['last_raw_at'])}."))

checks.append(("Phân loại", OK if t["masters"] and t["masters_categorized"] / t["masters"] >= 0.5 else WARN,
               f"{pct(t['masters_categorized'], t['masters'])} địa điểm có danh mục OpenOOH; "
               "phần còn lại vẫn dùng được nhưng không vào thống kê theo danh mục."))

st.dataframe(
    pd.DataFrame(checks, columns=["Hạng mục", "Trạng thái", "Chi tiết"]),
    hide_index=True, width="stretch",
    column_config={"Chi tiết": st.column_config.TextColumn(width="large")},
)

# ---------------------------------------------------------------- growth

st.subheader("Tăng trưởng 30 ngày gần nhất")
g = daily_growth(30).set_index("day")
left, right = st.columns(2)
with left:
    st.markdown("**Master tạo mỗi ngày** (theo lúc gộp trùng xong)")
    st.bar_chart(g["masters_new"], color=BAR, y_label="master mới", x_label="")
with right:
    st.markdown("**Bản ghi thô crawl được mỗi ngày**")
    st.bar_chart(g["raw_new"], color=BAR, y_label="bản ghi", x_label="")
st.caption(
    f"7 ngày qua: +{n(t['masters_7d'])} master, +{n(t['raw_7d'])} bản ghi thô. "
    "Hai cột không so trực tiếp được: một nơi có thể được crawl nhiều lần, và "
    "dedupe có thể gộp những bản ghi crawl từ nhiều tuần trước."
)

# ---------------------------------------------------------------- where / what

left, right = st.columns([3, 2])
with left:
    st.subheader("Theo tỉnh/thành")
    pc = province_coverage()
    pc["coverage"] = (pc["cells_done"] / pc["cells_total"].where(pc["cells_total"] > 0)).fillna(0)
    st.dataframe(
        pc[["name", "masters", "coverage"]],
        hide_index=True, width="stretch", height=420,
        column_config={
            "name": "Tỉnh/thành",
            "masters": st.column_config.NumberColumn("Địa điểm sạch", format="%d"),
            "coverage": st.column_config.ProgressColumn(
                "Độ phủ crawl", min_value=0.0, max_value=1.0, format="percent",
                help="Tỉ lệ ô lưới (tất cả danh mục) đã crawl xong.",
            ),
        },
    )
with right:
    st.subheader("Theo danh mục OpenOOH")
    cb = category_breakdown()
    total = int(cb["masters"].sum()) or 1
    cb["share"] = cb["masters"] / total
    st.dataframe(
        cb, hide_index=True, width="stretch", height=420,
        column_config={
            "category": "Danh mục",
            "masters": st.column_config.NumberColumn("Địa điểm", format="%d"),
            "share": st.column_config.ProgressColumn(
                "Tỉ lệ", min_value=0.0, max_value=1.0, format="percent",
            ),
        },
    )

# ---------------------------------------------------------------- details

with st.expander("Chi tiết kỹ thuật: nguồn dữ liệu và trạng thái job"):
    a, b = st.columns([3, 2])
    with a:
        st.markdown("**Bản ghi thô theo nguồn**")
        st.dataframe(
            per_source_stats(), hide_index=True, width="stretch",
            column_config={
                "code": "Nguồn", "name": "Tên",
                "enabled": st.column_config.CheckboxColumn("Bật"),
                "priority": st.column_config.NumberColumn("Ưu tiên"),
                "raw_count": st.column_config.NumberColumn("Bản ghi thô", format="%d"),
                "last_fetched": st.column_config.DatetimeColumn("Lần lấy cuối"),
            },
        )
    with b:
        st.markdown("**Job crawl theo trạng thái (toàn bộ lịch sử)**")
        st.dataframe(jobs_status_breakdown(), hide_index=True, width="stretch",
                     column_config={"status": "Trạng thái", "n": st.column_config.NumberColumn("Số job", format="%d")})
    st.markdown(
        "- **Bronze / raw_pois**: dữ liệu nguyên gốc, không bao giờ sửa.\n"
        "- **Silver / processed_pois**: mỗi bản ghi thô được làm sạch thành một dòng.\n"
        "- **Gold / master_pois**: các dòng silver cùng một địa điểm được gộp thành một master.\n"
        f"- Kế hoạch crawl: {n(c['rows_total'])} hàng (tỉnh × danh mục) — "
        f"{n(c['rows_done'])} xong, {n(c['rows_active'])} đang chạy/chờ, {n(c['rows_paused'])} tạm dừng."
    )
