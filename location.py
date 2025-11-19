import time
import webbrowser
import os
import winsound
from datetime import datetime, timezone
from locationsharinglib import Service
from geopy.distance import geodesic
from collections import deque

# ==============================================================================
# CẤU HÌNH HỆ THỐNG
# ==============================================================================
COOKIES_FILE = 'cookies.txt'
MY_EMAIL = 'nguyendangkhoa420614@gmail.com'

SAFE_RADIUS = 100
CHECK_INTERVAL = 30
SMOOTHING_WINDOW = 1  # Đã có Kalman lo việc làm mượt, nên để window=1 cho nhạy
HTML_FILE_NAME = "dashboard.html"

# Link tài nguyên
ALARM_SOUND_URL = "https://assets.mixkit.co/sfx/preview/mixkit-alarm-digital-clock-beep-989.mp3"
WARNING_IMG = "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExYjRkN2RlZTI5ZjFhY2E2ZjFhY2E2ZjFhY2E2ZiZlcD12MV9pbnRlcm5hbF9naWZzX2dpZklkJmN0PWc/26tP3M3iA3EKIkCSQ/giphy.gif"
SAFE_IMG = "https://cdn-icons-png.flaticon.com/512/148/148767.png"
OFFLINE_IMG = "https://cdn-icons-png.flaticon.com/512/564/564619.png"


# ==============================================================================
# MÔ HÌNH KALMAN FILTER (TOÁN HỌC CAO CẤP)
# ==============================================================================
class SimpleKalmanFilter:
    """
    Bộ lọc Kalman 1 chiều để ước lượng Vị trí và Vận tốc
    """

    def __init__(self, initial_x, initial_v, dt=1.0):
        # TRẠNG THÁI (State)
        self.x = initial_x  # Vị trí (Khoảng cách)
        self.v = initial_v  # Vận tốc
        self.dt = dt  # Bước thời gian (Time step)

        # ĐỘ KHÔNG CHẮC CHẮN (Covariance)
        self.P_xx = 10.0  # Sai số vị trí
        self.P_vv = 10.0  # Sai số vận tốc
        self.P_xv = 0.0  # Hiệp phương sai

        # THAM SỐ NHIỄU (Tùy chỉnh để bộ lọc nhạy hay đầm)
        self.Q_a = 0.1  # Nhiễu gia tốc (Process Noise) - Càng lớn càng tin vào thay đổi đột ngột
        self.R = 5.0  # Nhiễu đo đạc (Measurement Noise) - Càng lớn càng ít tin GPS (làm mượt hơn)

    def predict(self):
        """BƯỚC 1: DỰ BÁO (Dựa trên vật lý)"""
        # x_new = x + v*t
        self.x = self.x + self.v * self.dt
        # v_new = v (Giả định vận tốc không đổi)

        # Cập nhật độ không chắc chắn (P)
        self.P_xx += self.dt * (2 * self.P_xv + self.dt * self.P_vv) + self.Q_a * (self.dt ** 4) / 4
        self.P_xv += self.dt * self.P_vv + self.Q_a * (self.dt ** 3) / 2
        self.P_vv += self.Q_a * self.dt ** 2

        return self.x  # Trả về vị trí dự báo

    def update(self, z):
        """BƯỚC 2: HIỆU CHỈNH (Dựa trên số liệu đo thực tế)"""
        # Tính Kalman Gain (K) - Quyết định tin vào Dự báo hay tin vào GPS
        S = self.P_xx + self.R
        K_x = self.P_xx / S
        K_v = self.P_xv / S

        # Sai số giữa đo đạc và dự báo (Residual)
        y = z - self.x

        # Cập nhật trạng thái
        self.x += K_x * y
        self.v += K_v * y

        # Cập nhật độ không chắc chắn P
        P_xx_new = self.P_xx * (1 - K_x)
        P_xv_new = self.P_xv * (1 - K_x) - self.P_xx * K_v  # Xấp xỉ
        P_vv_new = self.P_vv - self.P_xv * K_v  # Xấp xỉ

        self.P_xx = P_xx_new
        self.P_xv = P_xv_new
        self.P_vv = P_vv_new

    def get_prediction_next_step(self, steps=1):
        """Dự báo tương lai xa hơn (cho cảnh báo sớm)"""
        return self.x + self.v * (self.dt * steps)


# ==============================================================================
# AUTO FIX COOKIE
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
    except:
        pass


# ==============================================================================
# CLASS XỬ LÝ (TÍCH HỢP KALMAN)
# ==============================================================================
class WorkerTracker:
    def __init__(self, name):
        self.name = name
        self.history_coords = deque(maxlen=SMOOTHING_WINDOW)
        self.anchor_pos = None
        self.is_ready = False

        # Tích hợp Kalman Filter
        self.kf = None

        # Biến báo cáo
        self.report_counter = 0
        self.last_pos_report = None

    def add_reading(self, lat, lon):
        self.history_coords.append((lat, lon))

    def get_current_gps_position(self):
        if not self.history_coords: return None
        return self.history_coords[-1]  # Lấy vị trí mới nhất

    def set_anchor_if_needed(self):
        if self.anchor_pos is None and len(self.history_coords) >= 1:
            self.anchor_pos = self.get_current_gps_position()
            self.last_pos_report = self.anchor_pos

            # Khởi tạo Kalman: Vị trí đầu = 0, Vận tốc đầu = 0
            self.kf = SimpleKalmanFilter(initial_x=0.0, initial_v=0.0, dt=1.0)

            self.is_ready = True
            return True
        return False

    def process_kalman(self, current_raw_dist):
        """Chạy quy trình dự báo & cập nhật của Kalman"""
        if not self.kf: return current_raw_dist, current_raw_dist

        # 1. Dự báo (Predict)
        self.kf.predict()

        # 2. Cập nhật với số liệu thực (Update)
        self.kf.update(current_raw_dist)

        # Lấy giá trị đã lọc nhiễu (Estimate)
        estimated_dist = self.kf.x

        # Lấy giá trị dự báo cho bước tiếp theo (Future)
        future_dist = self.kf.get_prediction_next_step(steps=1)

        # Trả về (Hiện tại đã lọc, Tương lai dự báo)
        # Đảm bảo không âm
        return max(0.0, estimated_dist), max(0.0, future_dist)


# ==============================================================================
# HÀM TẠO WEB HTML
# ==============================================================================
def generate_html(trackers_data, current_time):
    cards_html = ""
    for data in trackers_data:
        status_color = data['color']

        if data['status'] == "MẤT TÍN HIỆU":
            img_url = OFFLINE_IMG
            audio_tag = ""
        else:
            img_url = WARNING_IMG if data['is_alarm'] else SAFE_IMG
            audio_tag = f'<audio autoplay src="{ALARM_SOUND_URL}"></audio>' if data['is_alarm'] else ""

        map_link = f"https://www.google.com/maps/search/?api=1&query={data['lat']},{data['lon']}"

        cards_html += f"""
        <div class="card" style="border-top: 5px solid {status_color}; opacity: {0.6 if data['status'] == "MẤT TÍN HIỆU" else 1};">
            <div class="header">
                <h3>👷 {data['name']}</h3>
                <span class="badge" style="background:{status_color}">{data['status']}</span>
            </div>
            <div class="body">
                <div class="visual"><img src="{img_url}"></div>
                <div class="info">
                    <p><strong>Cách Mốc (Kalman):</strong> <span style="font-size: 1.2em; color: {status_color}">{data['dist']:.1f} m</span></p>
                    <p style="font-size: 0.8em; color: #666">Dự báo KF: {data['pred']:.1f} m</p>
                    <a href="{map_link}" target="_blank" class="btn-map">📍 Xem Bản Đồ</a>
                </div>
            </div>
            {audio_tag}
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="10">
        <title>ACSM Kalman Monitoring</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background-color: #eef2f5; padding: 20px; }}
            h1 {{ text-align: center; color: #2c3e50; }}
            .timestamp {{ text-align: center; color: #7f8c8d; margin-bottom: 30px; }}
            .container {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 20px; }}
            .card {{ background: white; width: 350px; padding: 20px; border-radius: 12px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); }}
            .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
            .badge {{ color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.85em; font-weight: bold; }}
            .body {{ display: flex; align-items: center; gap: 15px; }}
            .visual img {{ width: 80px; height: 80px; object-fit: cover; border-radius: 50%; border: 2px solid #eee; }}
            .btn-map {{ display: block; text-align: center; text-decoration: none; background: #3498db; color: white; padding: 8px; border-radius: 6px; margin-top: 10px; font-size: 0.9em; }}
        </style>
    </head>
    <body>
        <h1>🏗️ HỆ THỐNG GIÁM SÁT KALMAN FILTER</h1>
        <p class="timestamp">📡 Cập nhật lúc: {current_time}</p>
        <div class="container">{cards_html}</div>
    </body>
    </html>
    """
    with open(HTML_FILE_NAME, "w", encoding="utf-8") as f:
        f.write(html_content)


def console_alert_sound(duration=1000):
    try:
        winsound.Beep(2000, duration)
    except:
        pass


# ==============================================================================
# MAIN
# ==============================================================================
trackers = {}
first_run = True


def main():
    global first_run
    auto_fix_cookie_file()

    print(f"\n{'=' * 70}")
    print(f"🚀 HỆ THỐNG GIÁM SÁT CAO CẤP (KALMAN FILTER)")
    print(f"📍 Bán kính an toàn: {SAFE_RADIUS}m")
    print(f"🧠 Mô hình: Bộ lọc Kalman (Ước lượng trạng thái & Khử nhiễu)")
    print(f"📂 Web Dashboard: {os.path.abspath(HTML_FILE_NAME)}")
    print(f"{'=' * 70}\n")

    while True:
        try:
            current_time_str = datetime.now().strftime('%H:%M:%S')
            print(f"📡 [{current_time_str}] Đang phân tích dữ liệu...")

            service = Service(cookies_file=COOKIES_FILE, authenticating_account=MY_EMAIL)
            people = list(service.get_all_people())
            html_data_list = []

            if len(people) == 0: print("   ⚠️ Danh sách trống.")

            for person in people:
                name = person.full_name
                if MY_EMAIL in name or 'nguyendangkhoa' in name or name in ['Me', 'Bạn']: continue
                if not hasattr(person, 'latitude') or not person.latitude: continue

                if name not in trackers: trackers[name] = WorkerTracker(name)
                tracker = trackers[name]

                # Check thời gian
                data_timestamp = person.datetime
                now_utc = datetime.now(timezone.utc)
                if data_timestamp.tzinfo is None: data_timestamp = data_timestamp.replace(tzinfo=timezone.utc)
                age_minutes = (now_utc - data_timestamp).total_seconds() / 60

                tracker.add_reading(person.latitude, person.longitude)

                web_status = "..."
                web_color = "#95a5a6"
                web_is_alarm = False
                kf_dist = 0
                kf_future = 0

                if age_minutes > 5:
                    web_status = "MẤT TÍN HIỆU"
                    web_color = "#7f8c8d"
                    print(f"   ⚠️ {name}: Mất kết nối {int(age_minutes)} phút")
                else:
                    if not tracker.is_ready:
                        if tracker.set_anchor_if_needed():
                            print(f"   ⚓ {name}: Đã chốt MỐC KALMAN.")
                            web_status = "Đã chốt mốc"
                            web_color = "#2ecc71"
                        else:
                            print(f"   ⏳ {name}: Đang khởi tạo...")
                    else:
                        current_pos = tracker.get_current_gps_position()
                        # Khoảng cách thô (Raw GPS)
                        raw_dist = geodesic(tracker.anchor_pos, current_pos).meters

                        # --- CHẠY MÔ HÌNH KALMAN ---
                        kf_dist, kf_future = tracker.process_kalman(raw_dist)

                        if kf_dist > SAFE_RADIUS:
                            print(f"   ❌ CẢNH BÁO: {name} ra ngoài {kf_dist:.1f}m (KF)!")
                            console_alert_sound(1000)
                            web_status = "VI PHẠM RA NGOÀI"
                            web_color = "#e74c3c"
                            web_is_alarm = True
                        elif kf_future > SAFE_RADIUS:
                            print(f"   🚀 KALMAN DỰ BÁO: {name} sắp vi phạm ({kf_future:.1f}m)")
                            console_alert_sound(200)
                            web_status = "DỰ BÁO NGUY HIỂM"
                            web_color = "#f1c40f"
                        else:
                            web_status = "AN TOÀN"
                            web_color = "#2ecc71"

                            # So sánh vị trí
                            dist_moved = 0
                            if tracker.last_pos_report:
                                dist_moved = geodesic(tracker.last_pos_report, current_pos).meters
                            tracker.last_pos_report = current_pos

                            if dist_moved < 5.0:
                                print(f"   🔨 {name}: Đang làm việc (KF Dist: {kf_dist:.1f}m)")
                            else:
                                print(f"   ✅ {name}: Đang di chuyển (KF Dist: {kf_dist:.1f}m)")

                html_data_list.append({
                    'name': name, 'lat': person.latitude, 'lon': person.longitude,
                    'dist': kf_dist, 'pred': kf_future,
                    'status': web_status, 'color': web_color, 'is_alarm': web_is_alarm
                })

            generate_html(html_data_list, current_time_str)
            if first_run:
                webbrowser.open(f"file://{os.path.abspath(HTML_FILE_NAME)}")
                first_run = False

        except Exception as e:
            print(f"❌ Lỗi: {e}")
            if "Could not read" in str(e): auto_fix_cookie_file()

        print("-" * 70)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()