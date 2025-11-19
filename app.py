import streamlit as st
import time
import os
import sys
from streamlit.web import cli as stcli
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
SAFE_RADIUS = 100
SMOOTHING_WINDOW = 1
ALARM_SOUND_URL = "https://assets.mixkit.co/sfx/preview/mixkit-alarm-digital-clock-beep-989.mp3"


# ==============================================================================
# 2. HÀM HỖ TRỢ & BỘ LỌC KALMAN (GIỮ NGUYÊN)
# ==============================================================================
def auto_fix_cookie_file():
    if not os.path.exists(COOKIES_FILE): return
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        good_lines = [l for l in lines if
                      "# Netscape" in l or (l.strip() and not l.startswith('#') and len(l.split('\t')) >= 7)]
        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            f.writelines(good_lines)
    except Exception:
        pass


def play_laptop_sound():
    try:
        import winsound
        winsound.Beep(2500, 1000)
    except:
        pass


def play_web_sound_script():
    st.markdown(
        f"""
        <audio id="alarm_sound" src="{ALARM_SOUND_URL}" preload="auto"></audio>
        <script>
            var audio = document.getElementById('alarm_sound');
            if (audio) {{ audio.volume = 1.0; audio.loop = true; audio.play().catch(function(error) {{ }}); }}
        </script>
        """,
        unsafe_allow_html=True
    )


class SimpleKalmanFilter:
    def __init__(self, initial_x, initial_v, dt=1.0):
        self.x = initial_x;
        self.v = initial_v;
        self.dt = dt;
        self.P_xx = 10.0;
        self.P_vv = 10.0;
        self.P_xv = 0.0;
        self.Q_a = 0.1;
        self.R = 5.0

    def predict(self):
        self.x += self.v * self.dt;
        self.P_xx += self.dt * (2 * self.P_xv + self.dt * self.P_vv) + self.Q_a * (self.dt ** 4) / 4;
        self.P_xv += self.dt * self.P_vv + self.Q_a * (self.dt ** 3) / 2;
        self.P_vv += self.Q_a * self.dt ** 2
        return self.x

    def update(self, z):
        S = self.P_xx + self.R;
        K_x = self.P_xx / S;
        K_v = self.P_xv / S;
        y = z - self.x;
        self.x += K_x * y;
        self.v += K_v * y
        self.P_xx *= (1 - K_x);
        self.P_xv = (self.P_xv * (1 - K_x)) - (self.P_xx * K_v);
        self.P_vv -= self.P_xv * K_v

    def get_future(self): return self.x + self.v * self.dt


class WorkerTracker:
    def __init__(self, name):
        self.name = name;
        self.history = deque(maxlen=SMOOTHING_WINDOW);
        self.anchor_pos = None;
        self.kf = None;
        self.is_ready = False
        self.dist_history = deque(maxlen=30)

    def add(self, pos):
        self.history.append(pos)

    def set_anchor(self):
        if not self.anchor_pos and self.history:
            self.anchor_pos = self.history[-1];
            self.kf = SimpleKalmanFilter(0.0, 0.0);
            self.is_ready = True
            return True
        return False

    def process(self, current_pos):
        if not self.kf: return 0, 0
        raw = geodesic(self.anchor_pos, current_pos).meters;
        raw = 0.0 if raw < 5.0 else raw
        self.kf.predict();
        self.kf.update(raw)
        kf_dist = max(0.0, self.kf.x);
        kf_future = max(0.0, self.kf.get_future())
        self.dist_history.append(kf_dist)
        return kf_dist, kf_future


# ==============================================================================
# 3. GIAO DIỆN CHÍNH
# ==============================================================================
st.set_page_config(page_title="ACSM Monitor PRO", page_icon="🏗️", layout="wide")

# --- CUSTOM CSS CHO PHỐI MÀU HIỆN ĐẠI ---
st.markdown("""
    <style>
    /* BẮT BUỘC: Fix Full Screen */
    .main .block-container {
        padding: 1rem !important;
        max-width: 100%;
    }
    .stApp { background-color: #f4f6f8; }

    /* MÀU SẮC CHỦ ĐẠO (MODERN NAVY/TEAL) */
    .title-box { 
        background-color: #1a567c; /* Deep Navy */
        padding: 10px 0; border-radius: 8px; color: white; text-align: center; margin-bottom: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); 
    }

    /* MÀU CẢNH BÁO MỚI */
    .stAlert-danger { background-color: #c0392b !important; color: white; } /* Crimson Red */
    .stAlert-warning { background-color: #f39c12 !important; color: black; } /* Muted Orange */
    .stAlert-success { background-color: #1abc9c !important; color: white; } /* Muted Teal Green */

    /* BỎ CÁC ICON MẶC ĐỊNH STREAMLIT */
    .stExpander > div:first-child .icon { visibility: hidden; } 
    </style>
    """, unsafe_allow_html=True)

st.markdown('<div class="title-box"><h1>HỆ THỐNG GIÁM SÁT THÔNG MINH</h1></div>', unsafe_allow_html=True)
st.markdown(f"**Mô hình:** Kalman Filter AI | **Bán kính an toàn:** {SAFE_RADIUS}m")

if 'trackers' not in st.session_state: st.session_state.trackers = {}
auto_fix_cookie_file()
placeholder = st.empty()

while True:
    with placeholder.container():
        try:
            service = Service(cookies_file=COOKIES_FILE, authenticating_account=MY_EMAIL)
            people = list(service.get_all_people())
            now = datetime.now().strftime('%H:%M:%S')
            st.caption(f"📡 Cập nhật lần cuối: {now} (Tự động làm mới sau 3s)")

            if not people:
                st.warning("⚠️ Đang tìm thiết bị...")
                time.sleep(3);
                st.rerun()

            map_data = [];
            col1, col2 = st.columns([1, 1.5])

            with col1:
                st.subheader("Trạng thái Nhân sự")
                for person in people:
                    name = person.full_name
                    if MY_EMAIL in name or 'nguyendangkhoa' in name or name in ['Me', 'Bạn']: continue
                    if not person.latitude: continue

                    if name not in st.session_state.trackers: st.session_state.trackers[name] = WorkerTracker(name)
                    tracker = st.session_state.trackers[name]

                    dt_obj = person.datetime.replace(
                        tzinfo=timezone.utc) if person.datetime.tzinfo is None else person.datetime
                    age_min = (datetime.now(timezone.utc) - dt_obj).total_seconds() / 60

                    tracker.add((person.latitude, person.longitude))
                    map_data.append({'lat': person.latitude, 'lon': person.longitude, 'name': name, 'color': '#0000FF'})

                    with st.expander(f"📍 {name}", expanded=True):
                        if age_min > 5:
                            st.error(f"🚫 MẤT TÍN HIỆU ({int(age_min)} phút)")
                        elif not tracker.is_ready:
                            if tracker.set_anchor():
                                st.success("Đã chốt mốc ban đầu!")
                            else:
                                st.info("⏳ Đang khởi tạo...")
                        else:
                            kf_dist, kf_future = tracker.process(tracker.history[-1])
                            col_a, col_b = st.columns(2)

                            col_a.metric("Khoảng cách", f"{kf_dist:.1f}m")
                            col_b.metric("Dự báo (30s)", f"{kf_future:.1f}m", delta_color="off")

                            # --- LOGIC CẢNH BÁO ---
                            if kf_dist > SAFE_RADIUS:
                                st.markdown(
                                    '<div class="stAlert stAlert-danger">🚨 VI PHẠM AN TOÀN (Ra ngoài vùng)</div>',
                                    unsafe_allow_html=True)
                                play_web_sound_script()
                                play_laptop_sound()
                            elif kf_future > SAFE_RADIUS:
                                st.markdown('<div class="stAlert stAlert-warning">⚠️ DỰ BÁO XU HƯỚNG NGUY HIỂM</div>',
                                            unsafe_allow_html=True)
                                play_web_sound_script()
                                play_laptop_sound()
                            else:
                                st.markdown('<div class="stAlert stAlert-success">✅ ĐANG LÀM VIỆC</div>',
                                            unsafe_allow_html=True)

                            if len(tracker.dist_history) > 2:
                                df_chart = pd.DataFrame(
                                    {'Giây': range(len(tracker.dist_history)), 'Mét': list(tracker.dist_history)})
                                st.line_chart(df_chart, x='Giây', y='Mét', height=150)

            with col2:
                st.subheader("🗺️ Bản đồ Thời gian thực")
                if map_data:
                    st.map(pd.DataFrame(map_data), latitude='lat', longitude='lon', zoom=14)

                    st.subheader("📊 Phân tích Xu hướng Chung")
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

        except Exception as e:
            st.error(f"Đang kết nối... (Lỗi: {e})")
            if "Could not read" in str(e): auto_fix_cookie_file()

        time.sleep(3)
        st.rerun()

if __name__ == "__main__":
    if st._is_running_with_streamlit:
        pass
    else:
        sys.argv = ["streamlit", "run", os.path.abspath(__file__)]
        sys.exit(stcli.main())