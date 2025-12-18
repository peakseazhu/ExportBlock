from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from obspy.geodetics import gps2dist_azimuth
import os

# ========================
# 1. 定义地震事件
# ========================

event_time = UTCDateTime("2020-09-12T02:44:11")
event_lat = 38.748   
event_lon = 142.245
# 时间窗口：震前96小时 → 震后48小时（题目内容给的示例筛选是前72后24，故下的数据稍大一些）
start_time = event_time - 96 * 3600
end_time = event_time + 48 * 3600

# ========================
# 2. 获取周边所有可用台站（广撒网）
# ========================
client = Client("IRIS")

print("正在查询周边台站...")
inventory_full = client.get_stations(
    network="*",
    starttime=start_time,
    endtime=end_time,
    minlatitude=20.0,
    maxlatitude=45.0,
    minlongitude=80.0,
    maxlongitude=160.0,
    level="channel",
    channel="BHZ"
)

print(f"共找到 {len(inventory_full.get_contents()['stations'])} 个台站")

# ========================
# 3. 计算每个台站到震中的距离，并记录真实 location
# ========================
station_list = []

for net in inventory_full:
    for sta in net:
        if not hasattr(sta, 'latitude') or sta.latitude is None:
            continue
        # 获取 BHZ 通道的真实 location_code
        bhz_location = None
        for cha in sta:
            if cha.code == "BHZ":
                bhz_location = cha.location_code
                break
        if bhz_location is None:
            continue  # 没有 BHZ 通道（理论上不会发生）
        dist_m, _, _ = gps2dist_azimuth(event_lat, event_lon, sta.latitude, sta.longitude)
        dist_km = dist_m / 1000.0

        station_list.append({
            "network": net.code,
            "station": sta.code,  
            "location": bhz_location,
            "latitude": sta.latitude,
            "longitude": sta.longitude,
            "distance_km": dist_km
        })

# 按距离排序
station_list.sort(key=lambda x: x["distance_km"])

# ========================
# 4. 选择最近的 6 个台站
# ========================
K = 6
nearest_stations = station_list[:K]

print("\n距离汶川地震最近的 6 个台站：")
print(f"{'排名':<4} {'台站':<6} {'台网':<4} {'Loc':<5} {'距离(km)':<8} {'纬度':<8} {'经度':<8}")
print("-" * 60)
for i, s in enumerate(nearest_stations, 1):
    print(f"{i:<4} {s['station']:<6} {s['network']:<4} {s['location']:<5} {s['distance_km']:<8.1f} {s['latitude']:<8.3f} {s['longitude']:<8.3f}")

# ========================
# 5. 批量下载波形数据（使用真实 location）
# ========================
output_dir = "wenchuan_nearest_stations_2"
os.makedirs(output_dir, exist_ok=True)

for s in nearest_stations:
    try:
        print(f"\n正在下载 {s['network']}.{s['station']} ({s['distance_km']:.1f} km) ...")
        
        st = client.get_waveforms(
            network=s["network"],
            station=s["station"],
            location=s["location"],   # ← 使用真实 location ，一般有00、10或用通配符*
            channel="BHZ",#科研最常见通道 BH* 表示所有通道
            starttime=start_time,
            endtime=end_time
        )
        
        st.merge(fill_value=0)

        # 注入 SAC 头段
        for tr in st:
            tr.stats.sac = {
                'stla': s['latitude'],
                'stlo': s['longitude'],
                'evla': event_lat,
                'evlo': event_lon,
                'dist': s['distance_km'],
                'kevnm': 'Wenchuan',
                'nzyear': event_time.year,
                'nzjday': event_time.julday,
                'nzhour': event_time.hour,
                'nzmin': event_time.minute,
                'nzsec': event_time.second,
                'nzmsec': int(event_time.microsecond / 1000)
            }

        filename = f"{s['network']}.{s['station']}"
        st.write(os.path.join(output_dir, filename + ".mseed"), format="MSEED")
        st.write(os.path.join(output_dir, filename + ".sac"), format="SAC")
        print(f"✅ 成功保存: {filename}")

    except Exception as e:
        print(f"❌ 下载失败: {s['network']}.{s['station']}, 错误: {e}")

# ========================
# 6. 仅获取这 6 个台站的元数据，并保存为精简 StationXML
# ========================
network_list = [s["network"] for s in nearest_stations]
station_list_codes = [s["station"] for s in nearest_stations]

selected_inventory = client.get_stations(
    network=",".join(network_list),
    station=",".join(station_list_codes),
    starttime=start_time,
    endtime=end_time,
    level="channel",
    channel="BHZ"
)

xml_path = os.path.join(output_dir, "stations_inventory.xml")
selected_inventory.write(xml_path, format="STATIONXML")
print(f"\n✅ 已将 {len(nearest_stations)} 个台站的元数据保存至: {xml_path}")