-- NVC Smart Timetable: PostgreSQL schema (generated from nvc/models.py)

CREATE TABLE audit_logs (
	id SERIAL NOT NULL, 
	username VARCHAR(100), 
	action VARCHAR(100) NOT NULL, 
	entity VARCHAR(100), 
	detail JSON, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE departments (
	id SERIAL NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE room_types (
	id SERIAL NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE terms (
	id SERIAL NOT NULL, 
	year INTEGER NOT NULL, 
	semester INTEGER NOT NULL, 
	is_current BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_term UNIQUE (year, semester)
);

CREATE TABLE courses (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(300) NOT NULL, 
	course_type VARCHAR(20), 
	type_confirmed BOOLEAN NOT NULL, 
	theory_hours FLOAT, 
	practice_hours FLOAT, 
	credits FLOAT, 
	total_periods INTEGER, 
	consecutive INTEGER, 
	practice_room_types VARCHAR(300), 
	department_id INTEGER, 
	level VARCHAR(50), 
	is_general BOOLEAN, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_course_term_code UNIQUE (term_id, code), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE TABLE import_batches (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	filename VARCHAR(300) NOT NULL, 
	sheet VARCHAR(200), 
	entity VARCHAR(50) NOT NULL, 
	mapping JSON NOT NULL, 
	row_count INTEGER NOT NULL, 
	result JSON, 
	raw_rows JSON, 
	mode VARCHAR(20) NOT NULL, 
	created_by VARCHAR(100), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(term_id) REFERENCES terms (id)
);

CREATE TABLE rooms (
	id SERIAL NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(200), 
	number VARCHAR(50), 
	room_type_id INTEGER, 
	capacity INTEGER, 
	department_id INTEGER, 
	equipment TEXT, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(room_type_id) REFERENCES room_types (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE TABLE settings (
	id SERIAL NOT NULL, 
	term_id INTEGER, 
	key VARCHAR(100) NOT NULL, 
	value JSON, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_setting UNIQUE (term_id, key), 
	FOREIGN KEY(term_id) REFERENCES terms (id)
);

CREATE TABLE students (
	id SERIAL NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	department_id INTEGER, 
	status VARCHAR(50), 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE TABLE teachers (
	id SERIAL NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	department_id INTEGER, 
	position VARCHAR(200), 
	max_hours_week INTEGER, 
	max_hours_day INTEGER, 
	is_meeting_member BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);

CREATE TABLE timetables (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	name VARCHAR(200), 
	status VARCHAR(30) NOT NULL, 
	validation_status VARCHAR(30), 
	validation_report JSON, 
	solver_report JSON, 
	parent_id INTEGER, 
	scope_department_id INTEGER, 
	created_by VARCHAR(100), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	approved_by VARCHAR(100), 
	approved_at TIMESTAMP WITHOUT TIME ZONE, 
	published_at TIMESTAMP WITHOUT TIME ZONE, 
	lock_version INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(parent_id) REFERENCES timetables (id), 
	FOREIGN KEY(scope_department_id) REFERENCES departments (id)
);

CREATE TABLE change_requests (
	id SERIAL NOT NULL, 
	timetable_id INTEGER NOT NULL, 
	entry_id INTEGER NOT NULL, 
	requested_by VARCHAR(100) NOT NULL, 
	new_day INTEGER NOT NULL, 
	new_start INTEGER NOT NULL, 
	new_room_id INTEGER, 
	reason VARCHAR(500), 
	status VARCHAR(20) NOT NULL, 
	reviewed_by VARCHAR(100), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(timetable_id) REFERENCES timetables (id)
);

CREATE TABLE class_groups (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(200), 
	department_id INTEGER, 
	level VARCHAR(50), 
	advisor_teacher_id INTEGER, 
	size INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_group_term_code UNIQUE (term_id, code), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(advisor_teacher_id) REFERENCES teachers (id)
);

CREATE TABLE entries (
	id SERIAL NOT NULL, 
	timetable_id INTEGER NOT NULL, 
	day INTEGER NOT NULL, 
	start_period INTEGER NOT NULL, 
	end_period INTEGER NOT NULL, 
	start_time VARCHAR(5) NOT NULL, 
	end_time VARCHAR(5) NOT NULL, 
	entry_type VARCHAR(20) NOT NULL, 
	part VARCHAR(20), 
	course_id INTEGER, 
	room_id INTEGER, 
	schedule_mode VARCHAR(20) NOT NULL, 
	course_code VARCHAR(50), 
	course_name VARCHAR(300), 
	room_label VARCHAR(100), 
	created_by VARCHAR(100), 
	updated_by VARCHAR(100), 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(timetable_id) REFERENCES timetables (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(room_id) REFERENCES rooms (id)
);

CREATE INDEX ix_entry_tt_day ON entries (timetable_id, day);

CREATE TABLE joint_classes (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	course_id INTEGER NOT NULL, 
	teacher_id INTEGER, 
	group_ids JSON NOT NULL, 
	note VARCHAR(300), 
	PRIMARY KEY (id), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id)
);

CREATE TABLE room_unavailable (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	room_id INTEGER NOT NULL, 
	day INTEGER NOT NULL, 
	period INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_runavail UNIQUE (term_id, room_id, day, period), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(room_id) REFERENCES rooms (id)
);

CREATE TABLE teacher_qualifications (
	id SERIAL NOT NULL, 
	teacher_id INTEGER NOT NULL, 
	course_code VARCHAR(50) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_qual UNIQUE (teacher_id, course_code), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id)
);

CREATE TABLE teacher_unavailable (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	teacher_id INTEGER NOT NULL, 
	day INTEGER NOT NULL, 
	period INTEGER NOT NULL, 
	preferred BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tunavail UNIQUE (term_id, teacher_id, day, period, preferred), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id)
);

CREATE TABLE timetable_changes (
	id SERIAL NOT NULL, 
	timetable_id INTEGER NOT NULL, 
	action VARCHAR(30) NOT NULL, 
	entry_id INTEGER, 
	before JSON, 
	after JSON, 
	reason VARCHAR(500), 
	"user" VARCHAR(100), 
	undone BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(timetable_id) REFERENCES timetables (id)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	username VARCHAR(100) NOT NULL, 
	display_name VARCHAR(200), 
	password_hash VARCHAR(200) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	teacher_id INTEGER, 
	student_id INTEGER, 
	dept_scope JSON, 
	can_edit_timetable BOOLEAN NOT NULL, 
	must_change_password BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	failed_attempts INTEGER NOT NULL, 
	locked_until TIMESTAMP WITHOUT TIME ZONE, 
	last_login TIMESTAMP WITHOUT TIME ZONE, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id), 
	FOREIGN KEY(student_id) REFERENCES students (id)
);

CREATE TABLE assignments (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	teacher_id INTEGER NOT NULL, 
	course_id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	part VARCHAR(20) NOT NULL, 
	periods INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_assignment UNIQUE (term_id, teacher_id, course_id, group_id, part), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(group_id) REFERENCES class_groups (id)
);

CREATE TABLE enrollments (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	student_id INTEGER NOT NULL, 
	course_id INTEGER NOT NULL, 
	section_group_id INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_enrollment UNIQUE (student_id, course_id, term_id), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(student_id) REFERENCES students (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id), 
	FOREIGN KEY(section_group_id) REFERENCES class_groups (id)
);

CREATE TABLE entry_groups (
	id SERIAL NOT NULL, 
	entry_id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	group_code VARCHAR(50), 
	PRIMARY KEY (id), 
	FOREIGN KEY(entry_id) REFERENCES entries (id), 
	FOREIGN KEY(group_id) REFERENCES class_groups (id)
);

CREATE TABLE entry_teachers (
	id SERIAL NOT NULL, 
	entry_id INTEGER NOT NULL, 
	teacher_id INTEGER NOT NULL, 
	teacher_name VARCHAR(200), 
	PRIMARY KEY (id), 
	FOREIGN KEY(entry_id) REFERENCES entries (id), 
	FOREIGN KEY(teacher_id) REFERENCES teachers (id)
);

CREATE TABLE group_courses (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	course_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_group_course UNIQUE (group_id, course_id), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(group_id) REFERENCES class_groups (id), 
	FOREIGN KEY(course_id) REFERENCES courses (id)
);

CREATE TABLE student_memberships (
	id SERIAL NOT NULL, 
	term_id INTEGER NOT NULL, 
	student_id INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_membership UNIQUE (term_id, student_id), 
	FOREIGN KEY(term_id) REFERENCES terms (id), 
	FOREIGN KEY(student_id) REFERENCES students (id), 
	FOREIGN KEY(group_id) REFERENCES class_groups (id)
);

