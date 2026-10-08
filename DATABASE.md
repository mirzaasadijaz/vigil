# Database Tables — Core Sentry (ppe_test)

> Dumped from the live `ppe_test` schema. Alembic head: `d8e9f0a1b2c3`

## `users`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| username | varchar(100) | NO | |
| email | varchar(255) | YES | |
| hashed_password | varchar(255) | NO | |
| role | enum(admin, supervisor, operator, viewer) | NO | |
| is_active | bool | NO | |
| created_at | timestamptz | NO | now() |

## `employees`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| name | varchar(100) | NO | |
| employee_id | varchar(50) | NO | |
| photo | varchar(500) | YES | |
| extra_photos | varchar(500)[] | YES | |
| designation | varchar(100) | YES | |
| department | varchar(100) | YES | |
| email | varchar(150) | YES | |
| phone | varchar(40) | YES | |
| gender | varchar(20) | YES | |
| status | varchar(20) | NO | active |
| join_date | date | YES | |

## `face_embeddings`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| employee_id | int | NO | |
| vec | bytea | NO | |
| source | varchar(16) | NO | auto |
| quality | float | YES | |
| created_at | timestamptz | NO | now() |

## `rtsp_string_templates`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| name | varchar(100) | NO | |
| template_string | text | NO | |

## `nvr_devices`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| name | varchar(100) | NO | |
| ip_address | varchar(100) | NO | |
| port | int | NO | 554 |
| username | varchar(100) | YES | |
| password | varchar(100) | YES | |
| rtsp_template_id | int | YES | |

## `camera_devices`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| name | varchar(100) | NO | |
| ip_address | varchar(100) | YES | |
| username | varchar(100) | YES | |
| password | varchar(100) | YES | |
| channel | int | NO | 102 |
| process_frame_rate | int | NO | 10 |
| is_active | bool | NO | true |
| location | varchar(100) | YES | |
| department | varchar(100) | YES | |
| module_config | text | YES | |
| nvr_id | int | YES | |

## `alert_logs`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| employee_id | int | YES | |
| unknown_person | bool | NO | false |
| violation_type | varchar(255) | NO | |
| distance_score | float | YES | |
| likely_candidate | varchar(100) | YES | |
| snapshot | varchar(500) | YES | |
| timestamp | timestamptz | NO | now() |
| module | varchar(50) | YES | attendance |
| camera_id | int | YES | |
| event_type | varchar(50) | YES | |
| value | float | YES | |
| data | text | YES | |

## `attendance`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| employee_id | int | NO | |
| date | date | NO | |
| check_in | timestamptz | YES | |
| check_out | timestamptz | YES | |
| status | varchar(100) | NO | On Time |

## `menu_items`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| name | varchar(100) | NO | |
| category | varchar(50) | YES | meal |
| price | float | YES | 0.0 |
| icon | varchar(50) | YES | |
| is_active | bool | YES | true |

## `mess_sessions`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| employee_id | int | NO | |
| camera_id | int | YES | |
| status | varchar(20) | YES | open |
| started_at | timestamptz | YES | now() |
| closed_at | timestamptz | YES | |
| total | float | YES | 0.0 |

## `mess_session_items`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | auto |
| session_id | int | NO | |
| menu_item_id | int | YES | |
| name | varchar(100) | NO | |
| price | float | YES | 0.0 |
| qty | int | YES | 1 |
| amount | float | YES | 0.0 |

## `system_settings`
| Column | Type | Null | Default |
|---|---|---|---|
| id | int | NO | 1 |
| office_open_time | time | NO | 09:00 |
| office_close_time | time | NO | 18:00 |
| locations | text | NO | [] |
| departments | text | NO | [] |

## `alembic_version`
| Column | Type | Null |
|---|---|---|
| version_num | varchar | NO |
