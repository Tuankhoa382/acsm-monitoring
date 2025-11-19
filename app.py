import streamlit as st
import time
import os
from datetime import datetime, timezone
from locationsharinglib import Service
from geopy.distance import geodesic
from collections import deque

# ==============================================================================
# 1. CẤU HÌNH & XỬ LÝ COOKIE TRÊN MÂY
# ==============================================================================
# Trên Cloud, chúng ta không up file cookies.txt lên vì lộ bảo mật.
# Chúng ta sẽ dán nội dung cookie vào phần "Secrets" của Streamlit.
COOKIES_FILE = 'cookies.txt'


def setup_cookie_from_secrets():
    """Tạo file cookies.txt từ biến môi trường trên Cloud"""
    if not os.path.exists(COOKIES_FILE):
        # Nếu có trong Secrets (khi chạy trên Cloud)
        if 'COOKIE_CONTENT' in st.secrets:
            with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
                f.write(st.secrets['COOKIE_CONTENT'])
            return True
        # Nếu chạy Local máy bạn (đã có file sẵn)
        elif os.path.exists('cookies.txt'):
            return True
        else:
            return False
    return True


# Cấu hình Email quản lý (Lấy từ Secrets hoặc mặc định)
MY_EMAIL = st.secrets.get('MY_EMAIL', 'nguyendangkhoa420614@gmail.com')
SAFE_RADIUS = 100
SMOOTHING_WINDOW = 1

# Cấu hình giao diện Web
st.set_page_config(page_title="ACSM Monitor", page_icon="🏗️", layout="wide")


# ==============================================================================
# 2. MÔ HÌNH KALMAN FILTER (GIỮ NGUYÊN)
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


class WorkerTracker:
    def __init__(self, name):
        self.name = name
        self.history = deque(maxlen=SMOOTHING_WINDOW)
        self.anchor_pos = None;
        self.kf = None;
        self.is_ready = False

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
        raw = geodesic(self.anchor_pos, current_pos).meters
        self.kf.predict()
        self.kf.update(raw)
        return max(0.0, self.kf.x), max(0.0, self.kf.get_future())


# ==============================================================================
# 3. GIAO DIỆN CHÍNH (STREAMLIT APP)
# ==============================================================================
if 'trackers' not in st.session_state: st.session_state.trackers = {}

st.title("🏗️ GIÁM SÁT CÔNG TRƯỜNG ONLINE (KALMAN AI)")
st.markdown(f"**Hệ thống:** ACSM Cloud | **Bán kính:** {SAFE_RADIUS}m | **Trạng thái:** Real-time")

# Kiểm tra Cookie
if not setup_cookie_from_secrets():
    st.error("⚠️ Chưa cấu hình Cookie! Vui lòng vào Settings của Streamlit Cloud để thêm COOKIE_CONTENT.")
    st.stop()

# Container để tự động refresh
placeholder = st.empty()

while True:
    with placeholder.container():
        try:
            service = Service(cookies_file=COOKIES_FILE, authenticating_account=MY_EMAIL)
            people = list(service.get_all_people())
            now = datetime.now().strftime('%H:%M:%S')

            st.write(f"⏱️ **Cập nhật lúc:** {now}")

            if not people: st.warning("Không tìm thấy thiết bị nào.")

            # Chia cột để hiển thị thẻ đẹp
            cols = st.columns(3)
            idx = 0

            for person in people:
                name = person.full_name
                # Lọc quản lý
                if MY_EMAIL in name or 'nguyendangkhoa' in name or name in ['Me', 'Bạn']: continue
                if not person.latitude: continue

                # Xử lý Tracker
                if name not in st.session_state.trackers:
                    st.session_state.trackers[name] = WorkerTracker(name)
                tracker = st.session_state.trackers[name]

                # Check thời gian mất tín hiệu
                dt_obj = person.datetime.replace(
                    tzinfo=timezone.utc) if person.datetime.tzinfo is None else person.datetime
                age_min = (datetime.now(timezone.utc) - dt_obj).total_seconds() / 60

                tracker.add((person.latitude, person.longitude))

                # Hiển thị lên giao diện
                with cols[idx % 3]:
                    with st.container(border=True):
                        st.subheader(f"👷 {name}")

                        if age_min > 5:
                            st.error(f"🚫 MẤT TÍN HIỆU ({int(age_min)} phút)")
                        elif not tracker.is_ready:
                            if tracker.set_anchor():
                                st.success("⚓ Đã chốt mốc!")
                            else:
                                st.info("⏳ Đang khởi tạo...")
                        else:
                            kf_dist, kf_future = tracker.process(tracker.history[-1])

                            # Hiển thị số to
                            st.metric("Khoảng cách (KF)", f"{kf_dist:.1f} m", delta=f"Dự báo: {kf_future:.1f}m",
                                      delta_color="inverse")

                            if kf_dist > SAFE_RADIUS:
                                st.error("🚨 VI PHẠM RA NGOÀI")
                                # Âm thanh cảnh báo (HTML Trick)
                                st.markdown(
                                    """<audio autoplay src="https://assets.mixkit.co/sfx/preview/mixkit-alarm-digital-clock-beep-989.mp3">""",
                                    unsafe_allow_html=True)
                            elif kf_future > SAFE_RADIUS:
                                st.warning("⚠️ DỰ BÁO NGUY HIỂM")
                            else:
                                st.success("✅ ĐANG LÀM VIỆC")

                            # Link bản đồ
                            map_url = f"https://www.google.com/maps/search/?api=1&query={person.latitude},{person.longitude}"
                            st.link_button("📍 Xem Bản Đồ", map_url)
                idx += 1

        except Exception as e:
            st.error(f"Lỗi kết nối: {e}")

        # Tự động chạy lại sau 5s
        time.sleep(5)
        st.rerun()