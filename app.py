import streamlit as st
import time
import os
from datetime import datetime, timezone
from locationsharinglib import Service
from geopy.distance import geodesic
from collections import deque
import pandas as pd
import plotly.express as px

# ==============================================================================
# 1. CẤU HÌNH HỆ THỐNG
# ==============================================================================
COOKIES_FILE = 'cookies.txt'
MY_EMAIL = 'nguyendangkhoa420614@gmail.com'

# Cấu hình mặc định
SAFE_RADIUS = 100
SMOOTHING_WINDOW = 1

st.set_page_config(page_title="ACSM Monitor Pro", page_icon="🏗️", layout="wide")


# ==============================================================================
# 2. HÀM TỰ ĐỘNG SỬA COOKIE (QUAN TRỌNG ĐỂ KHÔNG LỖI)
# ==============================================================================
def auto_fix_cookie_file():
    if not os.path.exists(COOKIES_FILE): return
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        # Lọc chỉ lấy dòng chuẩn
        good_lines = [l for l in lines if
                      "# Netscape" in l or (l.strip() and not l.startswith('#') and len(l.split('\t')) >= 7)]
        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            f.writelines(good_lines)
    except:
        pass


# ==============================================================================
# 3. MÔ HÌNH KALMAN FILTER (PYTHON THUẦN)
# ==============================================================================
class SimpleKalmanFilter:
    def __init__(self, initial_x, initial_v, dt=1.0):
        self.x = initial_x;
        self.v = initial_v;
        self.dt = dt
        self.P_xx = 10.0;
        self.P_vv = 10.0;
        self.P_xv = 0.0
        self.Q_a = 0.1;
        self.R = 5.0

    def predict(self):
        self.x += self.v * self.dt
        self.P_xx += self.dt * (2 * self.P_xv + self.dt * self.P_vv) + self.Q_a * (self.dt ** 4) / 4
        self.P_xv += self.dt * self.P_vv + self.Q_a * (self.dt ** 3) / 2
        self.P_vv += self.Q_a * self.dt ** 2
        return self.x

    def update(self, z):
        S = self.P_xx + self.R
        K_x = self.P_xx / S
        K_v = self.P_xv / S
        y = z - self.x
        self.x += K_x * y
        self.v += K_v * y
        self.P_xx *= (1 - K_x)
        self.P_xv = (self.P_xv * (1 - K_x)) - (self.P_xx * K_v)
        self.P_vv -= self.P_xv * K_v

    def get_future(self): return self.x + self.v * self.dt


# ==============================================================================
# 4. CLASS XỬ LÝ DỮ LIỆU CÔNG NHÂN
# ==============================================================================
class WorkerTracker:
    def __init__(self, name):
        self.name = name
        self.history = deque(maxlen=SMOOTHING_WINDOW)
        self.anchor_pos = None;
        self.kf = None;
        self.is_ready = False
        # Lưu lịch sử để vẽ biểu đồ (30 điểm gần nhất)
        self.dist_history = deque(maxlen=30)

    def add(self, pos):
        self.history.append(pos)

    def set_anchor(self):
        if not self.anchor_pos and self.history:
            self.anchor_pos = self.history[-1]
            self.kf = SimpleKalmanFilter(0.0, 0.0)
            self.is_ready = True
            return True
        return False

    def process(self, current_pos):
        if not self.kf: return 0, 0

        # Tính khoảng cách thô
        raw = geodesic(self.anchor_pos, current_pos).meters

        # Lọc nhiễu nhỏ (dưới 5m coi như 0)
        if raw < 5.0: raw = 0.0

        # Kalman xử lý
        self.kf.predict()
        self.kf.update(raw)

        kf_dist = max(0.0, self.kf.x)
        kf_future = max(0.0, self.kf.get_future())

        # Lưu vào lịch sử biểu đồ
        self.dist_history.append(kf_dist)

        return kf_dist, kf_future


# ==============================================================================
# 5. GIAO DIỆN CHÍNH (STREAMLIT)
# ==============================================================================
# CSS tùy chỉnh cho đẹp
st.markdown("""
    <style>
    .stAlert { padding: 0.5rem 1rem; border-radius: 0.5rem; }
    .metric-card { border: 1px solid #ddd; padding: 15px; border-radius: 10px; margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🏗️ HỆ THỐNG GIÁM SÁT THÔNG MINH (ACSM PRO)")
st.markdown(f"**Mô hình:** Kalman Filter AI | **Bán kính an toàn:** {SAFE_RADIUS}m")

# Khởi tạo Session State
if 'trackers' not in st.session_state: st.session_state.trackers = {}

# Tự động sửa cookie trước khi chạy
auto_fix_cookie_file()

# Vùng chứa nội dung chính (Auto Refresh)
placeholder = st.empty()

while True:
    with placeholder.container():
        try:
            service = Service(cookies_file=COOKIES_FILE, authenticating_account=MY_EMAIL)
            people = list(service.get_all_people())
            now = datetime.now().strftime('%H:%M:%S')

            st.caption(f"📡 Cập nhật lần cuối: {now} (Tự động làm mới sau 3s)")

            if not people:
                st.warning("⚠️ Không tìm thấy thiết bị nào. Đang thử lại...")
                time.sleep(3)
                st.rerun()

            # Chuẩn bị dữ liệu bản đồ tổng quan
            map_data = []

            # Chia layout: Cột trái (Danh sách) - Cột phải (Bản đồ & Biểu đồ)
            col1, col2 = st.columns([1, 1.5])

            with col1:
                st.subheader("👷 Danh sách Nhân sự")

                for person in people:
                    name = person.full_name
                    # Lọc quản lý
                    if MY_EMAIL in name or 'nguyendangkhoa' in name or name in ['Me', 'Bạn']: continue
                    if not person.latitude: continue

                    # Khởi tạo Tracker
                    if name not in st.session_state.trackers:
                        st.session_state.trackers[name] = WorkerTracker(name)
                    tracker = st.session_state.trackers[name]

                    # Kiểm tra mất tín hiệu
                    dt_obj = person.datetime.replace(
                        tzinfo=timezone.utc) if person.datetime.tzinfo is None else person.datetime
                    age_min = (datetime.now(timezone.utc) - dt_obj).total_seconds() / 60

                    tracker.add((person.latitude, person.longitude))
                    map_data.append({'lat': person.latitude, 'lon': person.longitude, 'name': name, 'color': '#0000FF'})

                    # --- THẺ THÔNG TIN CÔNG NHÂN ---
                    with st.expander(f"📍 {name}", expanded=True):
                        if age_min > 5:
                            st.error(f"🚫 MẤT TÍN HIỆU ({int(age_min)} phút)")
                        elif not tracker.is_ready:
                            if tracker.set_anchor():
                                st.success("⚓ Đã chốt mốc ban đầu!")
                            else:
                                st.info("⏳ Đang khởi tạo...")
                        else:
                            kf_dist, kf_future = tracker.process(tracker.history[-1])

                            # Hiển thị số liệu
                            col_a, col_b = st.columns(2)
                            col_a.metric("Khoảng cách", f"{kf_dist:.1f}m")
                            col_b.metric("Dự báo (30s)", f"{kf_future:.1f}m", delta_color="inverse")

                            # Logic Cảnh báo
                            if kf_dist > SAFE_RADIUS:
                                st.error("🚨 VI PHẠM RA NGOÀI")
                                # Âm thanh
                                st.markdown(
                                    """<audio autoplay src="https://assets.mixkit.co/sfx/preview/mixkit-alarm-digital-clock-beep-989.mp3"></audio>""",
                                    unsafe_allow_html=True)
                            elif kf_future > SAFE_RADIUS:
                                st.warning("⚠️ DỰ BÁO NGUY HIỂM")
                            else:
                                st.success("✅ ĐANG LÀM VIỆC")

                            # Biểu đồ mini cho từng người
                            if len(tracker.dist_history) > 2:
                                df_chart = pd.DataFrame(
                                    {'Giây': range(len(tracker.dist_history)), 'Mét': list(tracker.dist_history)})
                                st.line_chart(df_chart, x='Giây', y='Mét', height=150)

            with col2:
                st.subheader("🗺️ Bản đồ Thời gian thực")
                if map_data:
                    df_map = pd.DataFrame(map_data)
                    st.map(df_map, latitude='lat', longitude='lon', zoom=14)

                    # Biểu đồ tổng quan (Plotly)
                    st.subheader("📊 Xu hướng di chuyển")
                    all_data = []
                    for t_name, t_obj in st.session_state.trackers.items():
                        if t_obj.is_ready and len(t_obj.dist_history) > 0:
                            for i, d in enumerate(t_obj.dist_history):
                                all_data.append({'Tên': t_name, 'Thời gian': i, 'Khoảng cách': d})

                    if all_data:
                        df_all = pd.DataFrame(all_data)
                        fig = px.line(df_all, x="Thời gian", y="Khoảng cách", color='Tên', height=300)
                        fig.add_hline(y=SAFE_RADIUS, line_dash="dash", line_color="red", annotation_text="Giới hạn")
                        st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Chưa có dữ liệu bản đồ.")

        except Exception as e:
            st.error(f"Đang kết nối... ({e})")
            # Nếu lỗi do file cookie hỏng, thử sửa lại
            if "Could not read" in str(e): auto_fix_cookie_file()

        time.sleep(3)
        st.rerun()

if __name__ == "__main__":
    pass