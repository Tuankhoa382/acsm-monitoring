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
from shapely.geometry import Point, Polygon
import numpy as np
import pydeck as pdk

# ==============================================================================
# 1. CẤU HÌNH HỆ THỐNG
# ==============================================================================
COOKIES_FILE = 'cookies.txt'
MY_EMAIL = 'nguyendangkhoa420614@gmail.com'
DEFAULT_SAFE_RADIUS = 100
SMOOTHING_WINDOW = 3
ALARM_SOUND_URL = "https://assets.mixkit.co/sfx/preview/mixkit-alarm-digital-clock-beep-989.mp3"


# ==============================================================================
# 2. HÀM HỖ TRỢ & CLASSES
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


def play_laptop_sound(freq=2500, duration=1000):
    try:
        import winsound;
        winsound.Beep(freq, duration)
    except:
        pass


def play_web_sound_script():
    st.markdown(
        f"""<audio id="alarm_sound" src="{ALARM_SOUND_URL}" preload="auto"></audio>
        <script>
            var audio = document.getElementById('alarm_sound');
            if (audio) {{ audio.volume = 1.0; audio.loop = true; audio.play().catch(function(error) {{ }}); }}
        </script>""",
        unsafe_allow_html=True)


def create_demo_danger_zone(center_lat, center_lon):
    offset = 0.0003
    p1 = (center_lat + offset, center_lon + offset)
    p2 = (center_lat + offset * 3, center_lon + offset)
    p3 = (center_lat + offset * 3, center_lon + offset * 3)
    p4 = (center_lat + offset, center_lon + offset * 3)
    return Polygon([(p1[1], p1[0]), (p2[1], p2[0]), (p3[1], p3[0]), (p4[1], p4[0])])


class SimpleKalmanFilter:
    def __init__(self, initial_x, initial_v, dt=30.0):
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

    def get_future(self):
        return self.x + self.v * self.dt


class WorkerTracker:
    def __init__(self, name, person_id):
        self.id = person_id
        self.name = name
        self.history = deque(maxlen=SMOOTHING_WINDOW)
        self.anchor_pos = None
        self.kf = None
        self.is_ready = False
        self.dist_history = deque(maxlen=30)
        self.last_pos_report = None
        self.danger_zone = None
        self.log_history = deque(maxlen=5)

    def update_name(self, new_name):
        self.name = new_name

    def add(self, pos):
        self.history.append(pos)

    def add_log(self, message, type='info'):
        timestamp = datetime.now().strftime('%H:%M:%S')
        icon = "ℹ️";
        color = "#333"
        if type == 'warn':
            icon = "🚨";
            color = "#d9534f"
        elif type == 'ok':
            icon = "✅";
            color = "#28a745"
        log_entry = f'<span style="color:{color}">[{timestamp}] {icon} {message}</span>'
        if not self.log_history or self.log_history[-1] != log_entry:
            self.log_history.append(log_entry)

    def set_anchor(self):
        if not self.anchor_pos and self.history:
            self.anchor_pos = self.history[-1]
            self.kf = SimpleKalmanFilter(0.0, 0.0, dt=30.0)
            self.is_ready = True
            self.last_pos_report = self.anchor_pos
            self.add_log("Đã chốt mốc & Vùng nguy hiểm.", 'ok')
            self.danger_zone = create_demo_danger_zone(self.anchor_pos[0], self.anchor_pos[1])
            return True
        return False

    def get_smoothed_position(self):
        if not self.history:
            return (0.0, 0.0)
        arr = np.array(self.history)
        return tuple(np.mean(arr, axis=0))

    def check_danger_zone(self, current_pos):
        return self.danger_zone.contains(Point(current_pos[1], current_pos[0])) if self.danger_zone else False

    def process(self, current_pos):
        if not self.kf: return 0, 0
        raw = geodesic(self.anchor_pos, current_pos).meters
        raw = 0.0 if raw < 5.0 else raw
        self.kf.predict()
        self.kf.update(raw)
        return max(0.0, self.kf.x), max(0.0, self.kf.get_future())


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return [int(hex_color[i:i + 2], 16) for i in (0, 2, 4)]


# ==============================================================================
# 3. GIAO DIỆN CHÍNH (LIGHT MODE)
# ==============================================================================
st.set_page_config(page_title="ACSM", page_icon="🛡️", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #f8f9fa; color: #212529; }
    .main .block-container { padding: 1rem !important; max-width: 100%; }
    .main-title { text-align: center; font-weight: 800; font-size: 2.5rem; color: #0056b3; margin-bottom: 5px; text-transform: uppercase; letter-spacing: 2px; }
    .sub-title { text-align: center; color: #6c757d; font-size: 0.9rem; margin-bottom: 30px; border-bottom: 1px solid #dee2e6; padding-bottom: 20px; }
    .worker-card { background-color: #ffffff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 20px; border: 1px solid #e9ecef; transition: all 0.3s ease; }
    .worker-card:hover { transform: translateY(-3px); box-shadow: 0 8px 15px rgba(0,0,0,0.1); border-color: #0056b3; }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; }
    .worker-name { font-size: 1.4rem; font-weight: 700; color: #343a40; display: flex; align-items: center; } 
    .worker-icon { margin-right: 10px; font-size: 1.4rem; }
    .status-badge { padding: 5px 12px; border-radius: 4px; color: #fff; font-weight: 700; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; }
    .badge-safe { background-color: #28a745; box-shadow: 0 0 5px rgba(40, 167, 69, 0.5); } 
    .badge-danger { background-color: #dc3545; animation: pulse-red 1s infinite; box-shadow: 0 0 10px rgba(220, 53, 69, 0.5); } 
    .badge-warning { background-color: #ffc107; color: #000; }
    .card-body { display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px; }
    .status-icon-container { width: 70px; height: 70px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: #f1f3f5; border: 2px solid #e9ecef; }
    .info-box { text-align: right; }
    .distance-label { font-size: 0.85rem; color: #adb5bd; font-weight: 600; }
    .distance-value { font-size: 2rem; font-weight: 800; font-family: 'Courier New', monospace; }
    .gps-text { font-size: 0.75rem; color: #6c757d; margin-top: 5px; font-family: monospace; }
    .log-box { background-color: #f8f9fa; border-radius: 6px; padding: 10px; font-size: 0.8rem; color: #333; font-family: 'Courier New', monospace; max-height: 100px; overflow-y: auto; border: 1px solid #dee2e6; }
    @keyframes pulse-red { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

SVG_SAFE = """<svg xmlns="http://www.w3.org/2000/svg" width="35" height="35" viewBox="0 0 24 24" fill="none" stroke="#28a745" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>"""
SVG_DANGER = """<svg xmlns="http://www.w3.org/2000/svg" width="35" height="35" viewBox="0 0 24 24" fill="none" stroke="#dc3545" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L15.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>"""

if 'trackers' not in st.session_state: st.session_state.trackers = {}
auto_fix_cookie_file()

st.markdown('<div class="main-title">HỆ THỐNG GIÁM SÁT AN NINH</div>', unsafe_allow_html=True)
now_str = datetime.now().strftime('%H:%M:%S')
st.markdown(f'<div class="sub-title">📡 TRẠNG THÁI: TRỰC TUYẾN (Chu kỳ quét: 30s) | CẬP NHẬT: {now_str}</div>',
            unsafe_allow_html=True)

with st.expander("⚙️ BẢNG ĐIỀU KHIỂN"):
    SAFE_RADIUS = st.slider("Thiết lập Bán kính An toàn (m)", -10, 300, DEFAULT_SAFE_RADIUS, 5)
    st.caption(f"Radius: {SAFE_RADIUS}m")

try:
    with st.spinner("🔄 Đang đồng bộ dữ liệu (Vui lòng chờ 30s)..."):
        service = Service(cookies_file='cookies.txt', authenticating_account=MY_EMAIL)
        people = list(service.get_all_people())

    if not people: st.warning("⚠️ NO SIGNAL DETECTED")

    map_data = [];
    danger_zones_data = []

    col1, col2 = st.columns([1, 2])

    with col1:
        for person in people:
            name = person.full_name
            p_id = person.id

            if MY_EMAIL in name or name in ['Me', 'Bạn']: continue
            if not person.latitude: continue

            if p_id not in st.session_state.trackers:
                st.session_state.trackers[p_id] = WorkerTracker(name, p_id)

            tracker = st.session_state.trackers[p_id]
            if tracker.name != name:
                tracker.update_name(name)

            dt_obj = person.datetime.replace(tzinfo=timezone.utc) if person.datetime.tzinfo is None else person.datetime
            age_min = (datetime.now(timezone.utc) - dt_obj).total_seconds() / 60
            tracker.add((person.latitude, person.longitude))

            current_smoothed_pos = (person.latitude, person.longitude)

            is_safe = True;
            status_text = "AN TOÀN";
            badge_class = "badge-safe";
            text_color = "#28a745";
            main_icon_svg = SVG_SAFE;
            dist_display = "0.0"

            if age_min > 5:
                is_safe = False;
                status_text = "MẤT TÍN HIỆU";
                badge_class = "badge-warning";
                text_color = "#d39e00";
                dist_display = "--"
                tracker.add_log(f"Mất kết nối ({int(age_min)}m)", 'warn')
            elif not tracker.is_ready:
                if tracker.set_anchor():
                    status_text = "ĐÃ CHỐT MỐC"
                else:
                    status_text = "KHỞI TẠO...";
                    is_safe = False
            else:
                kf_dist, kf_future = tracker.process(tracker.history[-1])
                dist_display = f"{kf_dist:.1f}"
                current_pos = tracker.history[-1]

                current_smoothed_pos = tracker.get_smoothed_position()

                if tracker.check_danger_zone(current_pos):
                    is_safe = False;
                    status_text = "VÙNG CẤM";
                    badge_class = "badge-danger";
                    text_color = "#dc3545";
                    main_icon_svg = SVG_DANGER
                    play_web_sound_script();
                    play_laptop_sound(3000, 1000);
                    tracker.add_log("XÂM NHẬP VÙNG CẤM", 'warn')
                elif kf_dist > SAFE_RADIUS:
                    is_safe = False;
                    status_text = "VI PHẠM";
                    badge_class = "badge-danger";
                    text_color = "#dc3545";
                    main_icon_svg = SVG_DANGER
                    play_web_sound_script();
                    play_laptop_sound();
                    tracker.add_log(f"Vượt rào: {kf_dist:.1f}m", 'warn')
                elif kf_future > SAFE_RADIUS:
                    is_safe = False;
                    status_text = "CẢNH BÁO";
                    badge_class = "badge-warning";
                    text_color = "#d39e00";
                    main_icon_svg = SVG_DANGER
                    play_web_sound_script();
                    play_laptop_sound();
                    tracker.add_log(f"Dự báo vi phạm", 'warn')
                else:
                    dist_moved = geodesic(tracker.last_pos_report, current_smoothed_pos).meters
                    tracker.last_pos_report = current_smoothed_pos
                    if dist_moved < 2.0:
                        tracker.add_log("Đang làm việc tại chỗ", 'ok')
                    else:
                        tracker.add_log(f"Di chuyển: {dist_moved:.1f}m", 'ok')

                    tracker.dist_history.append(kf_dist)

                if tracker.danger_zone:
                    danger_zones_data.append({
                        "name": f"DZ-{tracker.name}",
                        "path": list(tracker.danger_zone.exterior.coords)
                    })

            log_content = "<br>".join(list(tracker.log_history)[::-1]) if tracker.log_history else "..."
            short_id = p_id[-4:] if p_id else "N/A"

            card_html = f"""
            <div class="worker-card">
                <div class="card-header">
                    <div class="worker-name"><span class="worker-icon">👷</span>{name} <span style="font-size:0.8rem; color:#ccc; margin-left:10px">#{short_id}</span></div>
                    <div class="status-badge {badge_class}">{status_text}</div>
                </div>
                <div class="card-body">
                    <div class="status-icon-container">{main_icon_svg}</div>
                    <div class="info-box">
                        <div class="distance-label">KHOẢNG CÁCH</div>
                        <div class="distance-value" style="color: {text_color};">{dist_display} <span style="font-size:1rem">m</span></div>
                        <div class="gps-text">{person.latitude:.4f}, {person.longitude:.4f}</div>
                    </div>
                </div>
                <div class="log-box">{log_content}</div>
            </div>
            """
            st.markdown(card_html, unsafe_allow_html=True)

            map_color = '#28a745' if is_safe else '#dc3545'
            map_data.append(
                {'lat': current_smoothed_pos[0], 'lon': current_smoothed_pos[1],
                 'name': f"{name} (#{short_id})", 'color': map_color})

    with col2:
        if map_data:
            df_workers = pd.DataFrame(map_data)
            df_workers['color_rgb'] = df_workers['color'].apply(hex_to_rgb)

            # Lớp Vùng Nguy Hiểm
            layer_danger = pdk.Layer(
                "PolygonLayer",
                pd.DataFrame(danger_zones_data),
                get_polygon="path",
                get_fill_color=[255, 0, 0, 40],
                get_line_color=[255, 0, 0, 200],
                get_line_width=2,
                pickable=True,
                auto_highlight=True,
            )

            # [ĐÃ SỬA] Lớp Công Nhân: Chấm tròn tỉ lệ thực 10 mét
            layer_workers = pdk.Layer(
                "ScatterplotLayer",
                df_workers,
                get_position='[lon, lat]',
                get_color='color_rgb',
                get_radius=10,  # [QUAN TRỌNG] Bán kính thực tế 10 mét (đường kính 20m)
                radius_min_pixels=3,  # Giới hạn nhỏ nhất là 3px (để khi zoom xa vẫn thấy chấm li ti)
                radius_max_pixels=1000,  # Không giới hạn độ to khi zoom gần
                stroked=True,
                get_line_color=[0, 0, 0],  # Viền đen cho dễ nhìn
                get_line_width=2,
                pickable=True,
                auto_highlight=True,
            )

            view_state = pdk.ViewState(
                latitude=df_workers['lat'].mean(),
                longitude=df_workers['lon'].mean(),
                zoom=17,
                pitch=0
            )

            st.pydeck_chart(pdk.Deck(
                map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
                initial_view_state=view_state,
                layers=[layer_danger, layer_workers],
                tooltip={"text": "{name}"}
            ))

            all_chart_data = []
            for t_id, t_obj in st.session_state.trackers.items():
                if t_obj.is_ready and len(t_obj.dist_history) > 0:
                    for i, d in enumerate(t_obj.dist_history):
                        display_name = f"{t_obj.name} ({t_obj.id[-4:]})"
                        all_chart_data.append({'Tên': display_name, 'Thời gian': i, 'Khoảng cách': d})
            if all_chart_data:
                fig = px.line(pd.DataFrame(all_chart_data), x="Thời gian", y="Khoảng cách", color='Tên', height=300,
                              template="plotly_white")
                fig.add_hline(y=SAFE_RADIUS, line_dash="dash", line_color="red")
                fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=False),
                    yaxis=dict(showgrid=True, gridcolor='#e9ecef')
                )
                st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"❌ Lỗi: {e}")
    if "Could not read" in str(e): auto_fix_cookie_file()

time.sleep(30)
st.rerun()