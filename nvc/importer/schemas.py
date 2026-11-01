"""นิยามชนิดข้อมูลที่นำเข้าได้ ฟิลด์มาตรฐาน และคำพ้องความหมาย (ไทย/อังกฤษ)"""
from __future__ import annotations

# kind ของฟิลด์ใช้ตรวจเนื้อหาในคอลัมน์ประกอบการจับคู่
F = dict

ENTITIES: dict[str, dict] = {
    "department": {
        "label": "แผนกวิชา",
        "hints": ["แผนก", "department", "dept", "สาขา"],
        "fields": {
            "code": F(label="รหัสแผนก", required=True, kind="code", syn=["รหัสแผนก", "รหัสแผนกวิชา", "department code", "dept code", "dept id", "รหัสสาขา"]),
            "name": F(label="ชื่อแผนก", required=True, kind="text", syn=["ชื่อแผนก", "แผนก", "แผนกวิชา", "department", "department name", "dept name", "สาขาวิชา", "สาขา"]),
        },
    },
    "course": {
        "label": "รายวิชา",
        "hints": ["วิชา", "course", "subject", "หลักสูตร"],
        "fields": {
            "code": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "รหัสรายวิชา", "course id", "course code", "subject code", "subject id", "code"]),
            "name": F(label="ชื่อวิชา", required=True, kind="text", syn=["ชื่อวิชา", "ชื่อรายวิชา", "รายวิชา", "course name", "subject name", "subject", "course title", "title"]),
            "tpn": F(label="ท-ป-น (ทฤษฎี-ปฏิบัติ-หน่วยกิต)", kind="tpn", syn=["ท-ป-น", "ทปน", "ท-ป-นก", "ชั่วโมง ท-ป-น", "t-p-c", "tpc", "hours"]),
            "theory_hours": F(label="ชั่วโมงทฤษฎี", kind="num", syn=["ชั่วโมงทฤษฎี", "ทฤษฎี", "ชม.ทฤษฎี", "theory", "theory hours", "lecture hours", "ท"]),
            "practice_hours": F(label="ชั่วโมงปฏิบัติ", kind="num", syn=["ชั่วโมงปฏิบัติ", "ปฏิบัติ", "ชม.ปฏิบัติ", "practice", "practice hours", "lab hours", "ป"]),
            "credits": F(label="หน่วยกิต", kind="num", syn=["หน่วยกิต", "credit", "credits", "น"]),
            "total_periods": F(label="จำนวนคาบรวม", kind="int", syn=["จำนวนคาบ", "คาบรวม", "จำนวนคาบรวม", "คาบต่อสัปดาห์", "periods", "total periods", "periods per week"]),
            "consecutive": F(label="จำนวนคาบต่อเนื่อง", kind="int", syn=["คาบต่อเนื่อง", "จำนวนคาบต่อเนื่อง", "ต่อเนื่อง", "consecutive", "block", "block length"]),
            "course_type": F(label="ประเภทวิชา", kind="text", syn=["ประเภทวิชา", "ประเภท", "ลักษณะวิชา", "course type", "type", "category"]),
            "practice_room_types": F(label="ประเภทห้องปฏิบัติที่ต้องใช้", kind="text", syn=["ประเภทห้อง", "ห้องที่ต้องใช้", "ประเภทห้องปฏิบัติ", "room type", "required room", "lab type"]),
            "department": F(label="แผนกที่รับผิดชอบ", kind="text", syn=["แผนก", "แผนกวิชา", "แผนกที่รับผิดชอบ", "department", "dept", "สาขาวิชา"]),
            "level": F(label="ระดับชั้น", kind="text", syn=["ระดับชั้น", "ระดับ", "ชั้นปี", "level", "grade"]),
            "is_general": F(label="เป็นวิชาสามัญ", kind="bool", syn=["วิชาสามัญ", "สามัญ", "general", "is general"]),
        },
    },
    "teacher": {
        "label": "ครูผู้สอน",
        "hints": ["ครู", "อาจารย์", "teacher", "staff", "บุคลากร", "ผู้สอน"],
        "fields": {
            "code": F(label="รหัสครู", required=True, kind="code", syn=["รหัสครู", "รหัสอาจารย์", "รหัสผู้สอน", "รหัสบุคลากร", "teacher id", "teacher code", "staff id", "employee id"]),
            "name": F(label="ชื่อ-นามสกุล", required=True, kind="person", syn=["ชื่อ-นามสกุล", "ชื่อ-สกุล", "ชื่อครู", "ชื่ออาจารย์", "ผู้สอน", "ครูผู้สอน", "teacher name", "teacher", "full name", "name"]),
            "first_name": F(label="ชื่อ", kind="person", syn=["ชื่อ", "first name", "firstname"]),
            "last_name": F(label="นามสกุล", kind="person", syn=["นามสกุล", "สกุล", "last name", "lastname", "surname"]),
            "prefix": F(label="คำนำหน้า", kind="text", syn=["คำนำหน้า", "คำนำหน้าชื่อ", "title", "prefix"]),
            "department": F(label="แผนกวิชา", kind="text", syn=["แผนก", "แผนกวิชา", "สังกัด", "department", "dept"]),
            "position": F(label="ตำแหน่ง/หน้าที่", kind="text", syn=["ตำแหน่ง", "หน้าที่", "ตำแหน่งหน้าที่", "position", "role"]),
            "max_hours_week": F(label="ชั่วโมงสอนสูงสุด/สัปดาห์", kind="int", syn=["ชั่วโมงสอนสูงสุด", "ชม.สอนสูงสุด", "ภาระงานสูงสุด", "max hours", "max load", "max hours per week"]),
            "max_hours_day": F(label="ชั่วโมงสอนสูงสุด/วัน", kind="int", syn=["ชั่วโมงสอนสูงสุดต่อวัน", "สูงสุดต่อวัน", "max hours per day", "max per day"]),
            "meeting_member": F(label="เข้าประชุมหัวหน้า (ใช่/ไม่ใช่)", kind="bool", syn=["เข้าประชุม", "ประชุมหัวหน้า", "หัวหน้าแผนก", "หัวหน้างาน", "meeting", "head of department"]),
            "qualified_courses": F(label="รายวิชาที่สอนได้ (คั่นด้วย ,)", kind="text", syn=["รายวิชาที่สอนได้", "วิชาที่สอนได้", "can teach", "qualified courses"]),
        },
    },
    "room": {
        "label": "ห้องเรียน",
        "hints": ["ห้อง", "room", "อาคาร"],
        "fields": {
            "code": F(label="รหัสห้อง", required=True, kind="code", syn=["รหัสห้อง", "รหัสห้องเรียน", "room id", "room code"]),
            "name": F(label="ชื่อห้อง", kind="text", syn=["ชื่อห้อง", "ชื่อห้องเรียน", "room name"]),
            "number": F(label="หมายเลขห้อง", kind="code", syn=["หมายเลขห้อง", "เลขห้อง", "ห้อง", "room no", "room number", "room"]),
            "room_type": F(label="ประเภทห้อง", kind="text", syn=["ประเภทห้อง", "ประเภท", "room type", "type"]),
            "capacity": F(label="ความจุ", kind="int", syn=["ความจุ", "จำนวนที่นั่ง", "รองรับ", "capacity", "seats"]),
            "department": F(label="แผนกที่ดูแล", kind="text", syn=["แผนก", "แผนกที่ดูแล", "department", "dept"]),
            "equipment": F(label="อุปกรณ์/ทรัพยากรพิเศษ", kind="text", syn=["อุปกรณ์", "ทรัพยากร", "equipment", "resources"]),
        },
    },
    "group": {
        "label": "กลุ่มเรียน",
        "hints": ["กลุ่ม", "group", "class", "ห้องเรียน"],
        "fields": {
            "code": F(label="รหัสกลุ่มเรียน", required=True, kind="code", syn=["รหัสกลุ่ม", "รหัสกลุ่มเรียน", "กลุ่มเรียน", "กลุ่ม", "group id", "group code", "class group", "class id"]),
            "name": F(label="ชื่อกลุ่มเรียน", kind="text", syn=["ชื่อกลุ่ม", "ชื่อกลุ่มเรียน", "group name", "class name"]),
            "department": F(label="แผนกวิชา", kind="text", syn=["แผนก", "แผนกวิชา", "department", "dept", "สาขาวิชา"]),
            "level": F(label="ระดับชั้น", kind="text", syn=["ระดับชั้น", "ระดับ", "ชั้นปี", "level", "grade"]),
            "advisor": F(label="ครูที่ปรึกษา (รหัสครู)", kind="code", syn=["ครูที่ปรึกษา", "รหัสครูที่ปรึกษา", "อาจารย์ที่ปรึกษา", "advisor", "advisor id", "homeroom teacher"]),
            "size": F(label="จำนวนสมาชิก", kind="int", syn=["จำนวนนักเรียน", "จำนวนสมาชิก", "จำนวน", "size", "students", "number of students"]),
        },
    },
    "student": {
        "label": "นักเรียน",
        "hints": ["นักเรียน", "นักศึกษา", "student"],
        "fields": {
            "code": F(label="รหัสประจำตัวนักเรียน", required=True, kind="code", syn=["รหัสนักเรียน", "รหัสประจำตัว", "รหัสนักศึกษา", "เลขประจำตัว", "student id", "student code", "student no"]),
            "name": F(label="ชื่อ-นามสกุล", kind="person", syn=["ชื่อ-นามสกุล", "ชื่อ-สกุล", "ชื่อนักเรียน", "student name", "full name", "name"]),
            "first_name": F(label="ชื่อ", kind="person", syn=["ชื่อ", "first name", "firstname"]),
            "last_name": F(label="นามสกุล", kind="person", syn=["นามสกุล", "สกุล", "last name", "surname"]),
            "prefix": F(label="คำนำหน้า", kind="text", syn=["คำนำหน้า", "คำนำหน้าชื่อ", "title", "prefix"]),
            "group": F(label="รหัสกลุ่มเรียน", kind="code", syn=["รหัสกลุ่ม", "รหัสกลุ่มเรียน", "กลุ่มเรียน", "กลุ่ม", "group", "class group", "group id"]),
            "department": F(label="แผนกวิชา", kind="text", syn=["แผนก", "แผนกวิชา", "department", "สาขาวิชา"]),
            "status": F(label="สถานะการศึกษา", kind="text", syn=["สถานะ", "สถานะการศึกษา", "status"]),
        },
    },
    "group_course": {
        "label": "การลงทะเบียนรายวิชาของกลุ่มเรียน",
        "hints": ["ลงทะเบียนกลุ่ม", "แผนการเรียน", "group course", "registration", "ลงทะเบียน"],
        "fields": {
            "group": F(label="รหัสกลุ่มเรียน", required=True, kind="code", syn=["รหัสกลุ่ม", "รหัสกลุ่มเรียน", "กลุ่มเรียน", "กลุ่ม", "group", "class group", "group id"]),
            "course": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "รหัสรายวิชา", "course id", "course code", "subject code"]),
        },
    },
    "enrollment": {
        "label": "การลงทะเบียนเรียนรายบุคคล",
        "hints": ["ลงทะเบียน", "enroll", "registration"],
        "fields": {
            "student": F(label="รหัสนักเรียน", required=True, kind="code", syn=["รหัสนักเรียน", "รหัสประจำตัว", "รหัสนักศึกษา", "student id", "student code"]),
            "course": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "รหัสรายวิชา", "course id", "course code", "subject code"]),
            "group": F(label="กลุ่มที่เข้าเรียน (section)", kind="code", syn=["กลุ่มที่เรียน", "กลุ่มเรียน", "section", "รหัสกลุ่ม", "กลุ่ม", "group"]),
        },
    },
    "assignment": {
        "label": "การมอบหมายครูผู้สอน",
        "hints": ["มอบหมาย", "ผู้สอน", "ภาระงาน", "assign", "teaching load", "ครูผู้รับผิดชอบ"],
        "fields": {
            "teacher": F(label="รหัสครู", required=True, kind="code", syn=["รหัสครู", "รหัสผู้สอน", "รหัสอาจารย์", "teacher id", "teacher code"]),
            "course": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "รหัสรายวิชา", "course id", "course code", "subject code"]),
            "group": F(label="รหัสกลุ่มเรียน", required=True, kind="code", syn=["รหัสกลุ่ม", "รหัสกลุ่มเรียน", "กลุ่มเรียน", "กลุ่ม", "group", "class group", "group id"]),
            "periods": F(label="จำนวนคาบที่รับผิดชอบ", kind="int", syn=["จำนวนคาบ", "คาบที่รับผิดชอบ", "จำนวนคาบที่รับผิดชอบ", "periods", "hours"]),
            "part": F(label="ส่วนที่สอน (ทฤษฎี/ปฏิบัติ/ทั้งหมด)", kind="text", syn=["ส่วนที่สอน", "สอนส่วน", "ลักษณะการสอน", "part", "component"]),
        },
    },
    "teacher_unavailable": {
        "label": "เวลาไม่สะดวก/เวลาที่ต้องการของครู",
        "hints": ["ไม่สะดวก", "ไม่ว่าง", "unavailable", "availability", "busy"],
        "fields": {
            "teacher": F(label="รหัสครู", required=True, kind="code", syn=["รหัสครู", "รหัสผู้สอน", "teacher id", "teacher code"]),
            "day": F(label="วัน", required=True, kind="day", syn=["วัน", "วันที่ไม่สะดวก", "day", "weekday"]),
            "periods": F(label="คาบ หรือ ช่วงเวลา (เช่น 6-8 หรือ 13:00-15:00)", required=True, kind="periods", syn=["คาบ", "คาบที่", "ช่วงเวลา", "เวลา", "period", "periods", "time"]),
            "kind": F(label="ประเภท (ไม่สะดวก/ต้องการสอน)", kind="text", syn=["ประเภท", "ลักษณะ", "type", "kind"]),
        },
    },
    "qualification": {
        "label": "รายวิชาที่ครูสามารถสอนได้",
        "hints": ["สอนได้", "qualification", "can teach", "ความเชี่ยวชาญ"],
        "fields": {
            "teacher": F(label="รหัสครู", required=True, kind="code", syn=["รหัสครู", "teacher id", "teacher code"]),
            "course": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "course id", "course code", "subject code"]),
        },
    },
    "joint": {
        "label": "การเรียนร่วมหลายกลุ่ม",
        "hints": ["เรียนร่วม", "joint", "combined", "รวมกลุ่ม"],
        "fields": {
            "course": F(label="รหัสวิชา", required=True, kind="course_code", syn=["รหัสวิชา", "course id", "course code"]),
            "groups": F(label="กลุ่มที่เรียนร่วม (คั่นด้วย ,)", required=True, kind="text", syn=["กลุ่มที่เรียนร่วม", "กลุ่มเรียนร่วม", "กลุ่ม", "groups", "combined groups"]),
            "teacher": F(label="รหัสครู", kind="code", syn=["รหัสครู", "teacher id", "teacher code"]),
        },
    },
}

ENTITY_ORDER = [
    "department", "teacher", "room", "course", "group", "student",
    "group_course", "enrollment", "assignment", "qualification", "teacher_unavailable", "joint",
]

NONE_FIELD = "— ไม่ใช้คอลัมน์นี้ —"


def required_fields(entity: str) -> list[str]:
    fields = ENTITIES[entity]["fields"]
    req = [k for k, v in fields.items() if v.get("required")]
    return req


def alt_required_satisfied(entity: str, mapped: set[str]) -> list[str]:
    """คืนรายการฟิลด์สำคัญที่ยังขาด (รองรับชื่อแยก ชื่อ/นามสกุล)"""
    missing = []
    for f in required_fields(entity):
        if f in mapped:
            continue
        if f == "name" and entity in ("teacher", "student") and "first_name" in mapped:
            continue
        missing.append(f)
    if entity in ("teacher", "student") and "name" not in mapped and "first_name" not in mapped:
        if "name" not in missing:
            missing.append("name")
    return missing
