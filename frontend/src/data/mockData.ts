export const cx = (...classes: any[]) => classes.filter(Boolean).join(" ");

export const navItems = [
  { id: "overview", label: "Overview", short: "OV", category: "MAIN MENU" },
  { id: "operations", label: "Live Feed", short: "LF", category: "MAIN MENU" },
  { id: "alerts", label: "Alerts", short: "AL", category: "MAIN MENU" },
  { id: "intelligence", label: "Analytics", short: "AN", category: "MAIN MENU" },
  { id: "assets", label: "Assets & Access", short: "AA", category: "MAIN MENU" },
  { id: "chat", label: "AI Assistant", short: "AI", category: "MAIN MENU" },
  { id: "attendance", label: "Attendance", short: "AT", category: "MODULES" },
  { id: "mess", label: "Mess Management", short: "MS", category: "MODULES" },
  { id: "truck_count", label: "Truck Count", short: "TC", category: "MODULES" },
  { id: "ip_cameras", label: "IP Cameras", short: "IP", category: "CAMERAS" },
  { id: "nvr_dvr", label: "NVR / DVR", short: "NV", category: "CAMERAS" },
  { id: "rtsp_strings", label: "RTSP Strings", short: "RS", category: "CAMERAS" },
  { id: "admin", label: "Admin Settings", short: "AD", category: "SYSTEM" },
  { id: "employees", label: "Employees", short: "EM", category: "HR" },
];

export const employees = [
  { id: "EMP-001", name: "Ahmad Javed", role: "Security Supervisor", department: "Security", status: "Active", accessLevel: "Level 4" },
  { id: "EMP-002", name: "Sarah Connor", role: "Network Engineer", department: "IT", status: "Active", accessLevel: "Level 3" },
  { id: "EMP-003", name: "John Smith", role: "Warehouse Manager", department: "Logistics", status: "On Leave", accessLevel: "Level 2" },
  { id: "EMP-004", name: "Elena Rostova", role: "Data Scientist", department: "Analytics", status: "Active", accessLevel: "Level 3" },
  { id: "EMP-005", name: "Michael Chang", role: "Contractor", department: "Maintenance", status: "Suspended", accessLevel: "Level 1" },
];

export const topStats = [
  { label: "Total Cameras", value: "124", hint: "24 active in demo" },
  { label: "Live Alerts", value: "03", hint: "1 critical" },
  { label: "Uptime", value: "99.98%", hint: "Stable" },
  { label: "Avg Response", value: "420 ms", hint: "Alert dispatch" },
];

export const activityData = [
  { t: "08:00", detections: 12, accesses: 7, anomalies: 1 },
  { t: "10:00", detections: 18, accesses: 10, anomalies: 2 },
  { t: "12:00", detections: 27, accesses: 18, anomalies: 1 },
  { t: "14:00", detections: 34, accesses: 22, anomalies: 4 },
  { t: "16:00", detections: 30, accesses: 27, anomalies: 2 },
  { t: "18:00", detections: 20, accesses: 31, anomalies: 3 },
];

export const zoneTraffic = [
  { name: "Main Gate", value: 34 },
  { name: "Parking", value: 22 },
  { name: "Lobby", value: 18 },
  { name: "Server Room", value: 12 },
  { name: "Warehouse", value: 14 },
];

export const deviceHealth = [
  { name: "Online", value: 18, color: "var(--color-accent)" },
  { name: "Warning", value: 4, color: "var(--color-idle)" },
  { name: "Offline", value: 2, color: "var(--color-dnd)" },
];

export const devices = [
  { name: "CAM_01_WEST_LOBBY", status: "Online", zone: "Lobby", ip: "192.168.1.104", type: "Camera", fw: "v2.4.11-STABLE" },
  { name: "CAM_04_NORTH_PERIMETER", status: "Alert", zone: "Perimeter", ip: "192.168.1.107", type: "Camera", fw: "v2.4.10-STABLE" },
  { name: "CAM_08_LOADING_DOCK", status: "Online", zone: "Dock", ip: "192.168.1.112", type: "Camera", fw: "v2.4.11-STABLE" },
  { name: "SENSOR_02_FIRE_EXIT", status: "Offline", zone: "Fire Exit", ip: "192.168.1.145", type: "Sensor", fw: "v2.3.00-DEPRECATED" },
  { name: "DOOR_09_DATA_CENTER", status: "Online", zone: "Data Center", ip: "192.168.1.201", type: "Controller", fw: "v1.0.02-SECURITY" },
];

export const accessRules = [
  { title: "Face Recognition", description: "Biometric access for authorized staff" },
  { title: "Visitor QR Pass", description: "Temporary access through a QR credential" },
  { title: "Time Schedules", description: "Rules enforced by time window" },
  { title: "Role Based Access", description: "Permissions by operator, guard, visitor" },
];

export const alerts = [
  { title: "Unauthorized face at North Perimeter", severity: "Critical", status: "Escalated" },
  { title: "Camera CAM_03 buffering", severity: "Medium", status: "Monitoring" },
  { title: "Door anomaly at Data Center", severity: "High", status: "Open" },
];

export const liveTiles = [
  { name: "HQ Lobby B", zone: "Live", fps: 30, res: "1920×1080", time: "14:22:10 UTC", badge: "LIVE / MOTION_DETECTED" },
  { name: "Parking Level 02", zone: "Live", fps: 24, res: "2K", time: "14:22:08 UTC", badge: "LIVE" },
  { name: "Office West", zone: "Live", fps: 30, res: "1080p", time: "14:21:58 UTC", badge: "LIVE" },
  { name: "Warehouse_C3", zone: "Live", fps: 30, res: "2K", time: "14:21:50 UTC", badge: "LIVE" },
  { name: "Meeting_Room_04", zone: "Live", fps: 20, res: "1080p", time: "14:21:44 UTC", badge: "LIVE" },
  { name: "Perimeter_Wall_South", zone: "Alert", fps: 24, res: "1080p", time: "14:21:34 UTC", badge: "ANOMALY DETECTED" },
];

export const mapNodes = [
  { x: "18%", y: "22%", status: "stable" },
  { x: "48%", y: "51%", status: "alert" },
  { x: "78%", y: "78%", status: "stable" },
];

export const heatCells = [
  [2, 3, 5, 7, 6, 4],
  [1, 2, 6, 9, 8, 5],
  [1, 3, 7, 10, 9, 6],
  [2, 4, 6, 8, 7, 4],
  [1, 2, 4, 6, 5, 3],
  [0, 1, 2, 3, 3, 2],
];

export const alertParameters = [
  { id: "p01", tier: "Core", category: "Safety & Compliance", name: "Helmet / Hard Hat Non-Compliance", enabled: true },
  { id: "p02", tier: "Core", category: "Safety & Security", name: "Restricted Zone Unauthorized Intrusion", enabled: true },
  { id: "p03", tier: "Core", category: "Safety — Critical", name: "Fire, Smoke & Spark Detection", enabled: true },
  { id: "p04", tier: "Core", category: "Productivity", name: "Operator Absent from Active Machine", enabled: true },
  { id: "p05", tier: "Core", category: "Safety & Compliance", name: "PPE Full Kit Compliance Check", enabled: true },
  { id: "p06", tier: "Core", category: "Safety", name: "Slip & Fall Hazard — Spillage Detection", enabled: true },
  { id: "p07", tier: "Core", category: "Security & Loss Prevention", name: "Theft / Unauthorized Asset Removal", enabled: true },
  { id: "p08", tier: "Core", category: "Workforce Productivity", name: "Idle Time Detection — Operator Inactivity", enabled: true },
  { id: "p09", tier: "Core", category: "Safety — Critical", name: "Emergency Exit Blockage", enabled: true },
  { id: "p10", tier: "Core", category: "Safety", name: "Crowd / Overcrowding in Hazardous Zone", enabled: true },
  { id: "p11", tier: "Addon", category: "Safety Add-On", name: "Gloves & Mask Compliance — Chemical Zones", enabled: false },
  { id: "p12", tier: "Addon", category: "Process Efficiency", name: "Machine Running Without Any Operator", enabled: false },
  { id: "p13", tier: "Addon", category: "Workforce Health", name: "Fatigue / Low Activity Detection", enabled: false },
  { id: "p14", tier: "Addon", category: "Quality Control", name: "Improper Material Handling — Fabric Dragging", enabled: false },
  { id: "p15", tier: "Addon", category: "Quality Control", name: "Wrong Material Movement / Batch Mix-Up", enabled: false },
  { id: "p16", tier: "Addon", category: "Security & Compliance", name: "Contractor vs. Staff Compliance Violation", enabled: false },
  { id: "p17", tier: "Addon", category: "Security", name: "Unauthorized Machine Usage", enabled: false },
  { id: "p18", tier: "Addon", category: "Workforce Productivity", name: "Break Time Violation — Extended Absence", enabled: false },
  { id: "p19", tier: "Addon", category: "Safety", name: "Machine Guard Removal Detection", enabled: false },
  { id: "p20", tier: "Addon", category: "Process Efficiency", name: "Abnormal Machine Stoppage Frequency", enabled: false },
  { id: "p21", tier: "Addon", category: "Workforce Management", name: "Headcount Verification Per Shift", enabled: false },
  { id: "p22", tier: "Addon", category: "Quality / Compliance", name: "Housekeeping & Cleanliness Compliance", enabled: false },
  { id: "p23", tier: "Addon", category: "Security", name: "Visitor / Unauthorized Person in Production Zone", enabled: false },
  { id: "p24", tier: "Addon", category: "Workforce Health", name: "Posture Compliance — Role-Based Sitting/Standing", enabled: false },
  { id: "p25", tier: "Addon", category: "Safety", name: "Object Left Unattended in Walkway", enabled: false },
  { id: "p26", tier: "Addon", category: "Productivity", name: "Multi-Operator Clustering / Inefficiency Indicator", enabled: false },
  { id: "p27", tier: "Addon", category: "Process", name: "Shift Handover Compliance Verification", enabled: false },
  { id: "p28", tier: "Addon", category: "Security", name: "Suspicious Repeated Perimeter Approach", enabled: false },
  { id: "p29", tier: "Addon", category: "Safety", name: "Forklift / Vehicle Proximity to Pedestrian", enabled: false },
  { id: "p30", tier: "Addon", category: "Compliance / Process", name: "End-of-Day Zone Clearance Verification", enabled: false },
];
