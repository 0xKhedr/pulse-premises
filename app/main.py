import logging

logger = logging.getLogger(__name__)

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.schemas.core import Device, ProfileAlarmTemplateValues
from app.services.alarm_overrides import apply_overrides, load_overrides
from app.services.device_label import (
    LABEL_F_PORT,
    is_label_port,
    parse_label_uplink,
)
from app.services.device_state import build_device_state
from app.services.export_safety import redact_secrets
from app.services.profile_learning import plan_learned_fields
from app.services.thingsboard import tb_client
from app.services.ttn import ttn_client
from app.services.websockets import manager

os.makedirs('uploads', exist_ok=True)
load_dotenv()

MAIN_EVENT_LOOP = None


def schedule_broadcast(
    message,
    client_id=None,
    site_id=None,
    building_id=None,
    floor_id=None,
):
    """
    Fire a websocket broadcast from either the event loop or a worker thread.

    Blocking work now runs in a threadpool, where asyncio.create_task() has no
    running loop to attach to, so the coroutine is handed back to the main
    loop instead.
    """
    coro = manager.broadcast(
        message,
        client_id=client_id,
        site_id=site_id,
        building_id=building_id,
        floor_id=floor_id,
    )
    try:
        asyncio.get_running_loop().create_task(coro)
        return
    except RuntimeError:
        pass

    if MAIN_EVENT_LOOP is not None and not MAIN_EVENT_LOOP.is_closed():
        asyncio.run_coroutine_threadsafe(coro, MAIN_EVENT_LOOP)
    else:
        coro.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_EVENT_LOOP
    MAIN_EVENT_LOOP = asyncio.get_running_loop()
    from app.db.connection import run_migrations
    run_migrations()
    if settings.PREVIEW_MODE:
        from app.services.preview import preview_heartbeat, verify_preview_database
        verify_preview_database()
        task = asyncio.create_task(preview_heartbeat())
    else:
        task = asyncio.create_task(offline_watchdog())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title='LILYGO Provisioning Server', lifespan=lifespan)
templates = Jinja2Templates(directory='templates')
from app.config import settings

# CORS_ORIGINS existed in .env but was never wired in; the middleware was
# hardcoded to '*', which let any site read every unauthenticated endpoint.
_cors_origins = [
    origin.strip()
    for origin in (settings.CORS_ORIGINS or '').split(',')
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=_cors_origins,
        allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
else:
    # No configured front-end origin: same-origin only. Cross-origin browser
    # callers get no CORS headers rather than a blanket allow.
    app.add_middleware(CORSMiddleware, allow_origins=[], allow_credentials=
        False, allow_methods=['*'], allow_headers=['*'])
DB = settings.DB_FILE
UPLOAD_DIR = 'uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount('/uploads', StaticFiles(directory=UPLOAD_DIR), name='uploads')
if os.path.isdir('static'):
    app.mount('/static', StaticFiles(directory='static'), name='static')
TTN_BASE = settings.TTN_BASE_URL.rstrip('/')
APP_ID = settings.TTN_APP_ID
API_KEY = settings.TTN_API_KEY
JOIN_EUI = settings.JOIN_EUI.upper()
LORAWAN_APP_KEY = settings.LORAWAN_APP_KEY.upper()
FREQUENCY_PLAN_ID = settings.LORAWAN_FREQUENCY_PLAN_ID
LORAWAN_VERSION = settings.LORAWAN_VERSION
LORAWAN_PHY_VERSION = settings.LORAWAN_PHY_VERSION
TTN_HOST = TTN_BASE.replace('https://', '').replace('http://', '')
THINGSBOARD_URL = os.getenv('THINGSBOARD_URL', 'http://localhost:8080').rstrip(
    '/')
TB_USERNAME = os.getenv('TB_USERNAME', 'tenant@thingsboard.org')
TB_PASSWORD = os.getenv('TB_PASSWORD', 'tenant')
TB_TOKEN_CACHE = None
ALERT_EMAIL_ENABLED = os.getenv('ALERT_EMAIL_ENABLED', 'false').lower(
    ) == 'true'
ALERT_EMAIL_FROM = os.getenv('ALERT_EMAIL_FROM', '')
ALERT_EMAIL_PASSWORD = os.getenv('ALERT_EMAIL_PASSWORD', '')
ALERT_EMAIL_TO = os.getenv('ALERT_EMAIL_TO', '')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'change-me')
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@system.local').strip().lower()
SESSION_TTL_SECONDS = int(os.getenv('SESSION_TTL_SECONDS', '28800'))
PASSWORD_PBKDF2_ITERATIONS = int(os.getenv('PASSWORD_PBKDF2_ITERATIONS',
    '200000'))
SESSION_COOKIE_NAME = 'sbms_session'
LOCAL_TEST_MODE = os.getenv('LOCAL_TEST_MODE', 'false').lower() == 'true'
HEADERS = {'Authorization': f'Bearer {API_KEY}', 'Content-Type':
    'application/json'}
FORMATTERS = {'environment':
    """
function decodeUplink(input) {
  var bytes = input.bytes;

  if (bytes.length !== 4) {
    return { errors: ["Invalid payload length. Expected 4 bytes."] };
  }

  var tempRaw = (bytes[0] << 8) | bytes[1];
  var humRaw = (bytes[2] << 8) | bytes[3];

  if (tempRaw & 0x8000) {
    tempRaw = tempRaw - 0x10000;
  }

  return {
    data: {
      temperature: tempRaw / 100.0,
      humidity: humRaw / 100.0
    }
  };
}
"""
    , 'occupancy':
    """
function decodeUplink(input) {
  var bytes = input.bytes;

  if (bytes.length !== 1) {
    return { errors: ["Invalid payload length. Expected 1 byte."] };
  }

  return {
    data: {
      motion: bytes[0] === 1
    }
  };
}
"""
    , 'safety':
    """
function decodeUplink(input) {
  var bytes = input.bytes;

  if (bytes.length !== 1) {
    return { errors: ["Invalid payload length. Expected 1 byte."] };
  }

  return {
    data: {
      alarm: bytes[0] === 1
    }
  };
}
"""
    , 'energy':
    """
function decodeUplink(input) {
  var bytes = input.bytes;

  if (bytes.length !== 6) {
    return { errors: ["Invalid payload length. Expected 6 bytes."] };
  }

  var voltageRaw = (bytes[0] << 8) | bytes[1];
  var currentRaw = (bytes[2] << 8) | bytes[3];
  var powerRaw   = (bytes[4] << 8) | bytes[5];

  return {
    data: {
      voltage: voltageRaw / 100.0,
      current: currentRaw / 100.0,
      power: powerRaw / 10.0
    }
  };
}


"""
    , 'multi':
    """
function decodeUplink(input) {
  var bytes = input.bytes;

  if (bytes.length !== 13) {
    return { errors: ["Invalid multi payload length. Expected 13 bytes."] };
  }

  var tempRaw = (bytes[0] << 8) | bytes[1];
  var humRaw = (bytes[2] << 8) | bytes[3];

  if (tempRaw & 0x8000) {
    tempRaw = tempRaw - 0x10000;
  }

  var motion = bytes[4] === 1;
  var alarm = bytes[5] === 1;

  var voltageRaw = (bytes[6] << 8) | bytes[7];
  var currentRaw = (bytes[8] << 8) | bytes[9];
  var powerRaw = (bytes[10] << 8) | bytes[11];

  var battery = bytes[12];

  return {
    data: {
      temperature: tempRaw / 100.0,
      humidity: humRaw / 100.0,
      motion: motion,
      alarm: alarm,
      voltage: voltageRaw / 100.0,
      current: currentRaw / 100.0,
      power: powerRaw / 10.0,
      battery: battery
    }
  };
}
"""
    }
ALLOWED_NODE_TYPES = {'environment', 'occupancy', 'safety', 'energy', 'multi'}
PROFILE_CODE_PATTERN = re.compile('^[A-Z][A-Z0-9_]{2,63}$')
PROFILE_KEY_PATTERN = re.compile('^[a-z][a-z0-9_]{1,63}$')
PROFILE_ALLOWED_STATUSES = {'draft', 'active', 'deprecated', 'archived'}
PROFILE_ALLOWED_DATA_TYPES = {'number', 'integer', 'boolean', 'string'}
PROFILE_ALLOWED_OPERATORS = {'>', '>=', '<', '<=', '==', '!=', 'between',
    'outside', 'contains'}
PROFILE_ALLOWED_SEVERITIES = {'info', 'warning', 'critical'}


def profile_json_load(value, default):
    """
    Safely convert a JSON database value into a Python value.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def profile_unique_string_list(values):
    """
    Return a clean list of unique, lowercase string values.
    """
    result = []
    seen = set()
    for value in (values or []):
        clean_value = str(value).strip().lower()
        if not clean_value:
            continue
        if clean_value in seen:
            continue
        seen.add(clean_value)
        result.append(clean_value)
    return result


def profile_value_to_boolean(value, default=False):
    """
    Convert common JSON, HTML and database values into a Boolean.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'enabled'}


def profile_optional_integer(value, field_name, errors, minimum=None,
    maximum=None):
    """
    Validate and normalize an optional integer.
    """
    if value is None or value == '':
        return None
    try:
        result = int(value)
    except Exception:
        errors.append(f'{field_name} must be an integer')
        return None
    if minimum is not None and result < minimum:
        errors.append(
            f'{field_name} must be greater than or equal to {minimum}')
    if maximum is not None and result > maximum:
        errors.append(f'{field_name} must be less than or equal to {maximum}')
    return result


def profile_optional_float(value, field_name, errors):
    """
    Validate and normalize an optional numeric value.
    """
    if value is None or value == '':
        return None
    try:
        return float(value)
    except Exception:
        errors.append(f'{field_name} must be numeric')
        return None


def calculate_sensor_profile_checksum(profile_definition: dict) ->str:
    """
    Create a deterministic checksum for a complete profile definition.
    """
    checksum_payload = {'profile_code': profile_definition.get(
        'profile_code'), 'profile_name': profile_definition.get(
        'profile_name'), 'profile_version': profile_definition.get(
        'profile_version'), 'node_type': profile_definition.get('node_type'
        ), 'description': profile_definition.get('description'),
        'capabilities': profile_definition.get('capabilities', []),
        'configuration_schema': profile_definition.get(
        'configuration_schema', {}), 'payload_encoder_key':
        profile_definition.get('payload_encoder_key'), 'payload_version':
        profile_definition.get('payload_version'), 'f_port':
        profile_definition.get('f_port'), 'uplink_interval_seconds':
        profile_definition.get('uplink_interval_seconds'),
        'ttn_formatter_code': profile_definition.get('ttn_formatter_code'),
        'ttn_formatter_type': profile_definition.get('ttn_formatter_type'),
        'tb_device_profile_name': profile_definition.get(
        'tb_device_profile_name'), 'icon_type': profile_definition.get(
        'icon_type'), 'icon_color': profile_definition.get('icon_color'),
        'fields': profile_definition.get('fields', []), 'rules':
        profile_definition.get('rules', []), 'sensors': profile_definition.
        get('sensors', [])}
    canonical_json = json.dumps(checksum_payload, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def list_sensor_profile_summaries(conn: sqlite3.Connection,
    include_disabled: bool=True):
    """
    Return lightweight profile records for dropdowns and list pages.
    """
    sql = """
        SELECT
            p.id,
            p.profile_code,
            p.profile_name,
            p.profile_version,
            p.node_type,
            p.description,
            p.capabilities_json,
            p.payload_encoder_key,
            p.payload_version,
            p.f_port,
            p.uplink_interval_seconds,
            p.tb_device_profile_name,
            p.icon_type,
            p.icon_color,
            p.status,
            p.enabled,
            p.is_system,
            p.schema_checksum,
            p.created_at,
            p.updated_at,

            (
                SELECT COUNT(*)
                FROM sensor_profile_fields f
                WHERE f.profile_id = p.id
            ) AS field_count,

            (
                SELECT COUNT(*)
                FROM sensor_profile_rules r
                WHERE r.profile_id = p.id
            ) AS rule_count,

            (
                SELECT COUNT(*)
                FROM sensor_profile_sensors s
                WHERE s.profile_id = p.id
            ) AS sensor_count,

            (
                SELECT COUNT(*)
                FROM devices d
                WHERE d.profile_id = p.id
                   OR (
                        d.profile_id IS NULL
                        AND d.profile_code = p.profile_code
                   )
            ) AS device_count

        FROM sensor_profiles p
    """
    parameters = []
    if not include_disabled:
        sql += """
            WHERE p.enabled = 1
              AND p.status = 'active'
        """
    sql += """
        ORDER BY
            CASE p.status
                WHEN 'active' THEN 1
                WHEN 'draft' THEN 2
                WHEN 'deprecated' THEN 3
                WHEN 'archived' THEN 4
                ELSE 5
            END,
            p.profile_name,
            p.profile_version DESC
    """
    rows = conn.execute(sql, parameters).fetchall()
    result = []
    for row in rows:
        profile = dict(row)
        profile['capabilities'] = profile_json_load(profile.pop(
            'capabilities_json', None), [])
        profile['enabled'] = bool(profile.get('enabled'))
        profile['is_system'] = bool(profile.get('is_system'))
        profile['can_delete'] = not profile['is_system'] and int(profile.
            get('device_count') or 0) == 0
        result.append(profile)
    return result


def get_sensor_profile_row(conn: sqlite3.Connection, profile_identifier):
    """
    Find a profile using either its numeric ID or profile code.
    """
    if profile_identifier is None:
        return None
    if isinstance(profile_identifier, int):
        return conn.execute(
            """
            SELECT *
            FROM sensor_profiles
            WHERE id = ?
            LIMIT 1
            """
            , (profile_identifier,)).fetchone()
    clean_identifier = str(profile_identifier).strip()
    if clean_identifier.isdigit():
        return conn.execute(
            """
            SELECT *
            FROM sensor_profiles
            WHERE id = ?
            LIMIT 1
            """
            , (int(clean_identifier),)).fetchone()
    return conn.execute(
        """
        SELECT *
        FROM sensor_profiles
        WHERE UPPER(profile_code) = UPPER(?)
        LIMIT 1
        """
        , (clean_identifier,)).fetchone()


def get_sensor_profile_detail(conn: sqlite3.Connection, profile_identifier,
    include_formatter: bool=True):
    """
    Return one complete profile with fields, rules, sensors,
    compatibility information and usage information.
    """
    profile_row = get_sensor_profile_row(conn, profile_identifier)
    if not profile_row:
        return None
    profile = dict(profile_row)
    profile['capabilities'] = profile_json_load(profile.pop(
        'capabilities_json', None), [])
    profile['configuration_schema'] = profile_json_load(profile.pop(
        'configuration_schema_json', None), {})
    profile['enabled'] = bool(profile.get('enabled'))
    profile['is_system'] = bool(profile.get('is_system'))
    if not include_formatter:
        profile.pop('ttn_formatter_code', None)
    field_rows = conn.execute(
        """
        SELECT *
        FROM sensor_profile_fields
        WHERE profile_id = ?
        ORDER BY display_order, payload_order, id
        """
        , (profile['id'],)).fetchall()
    fields = []
    for row in field_rows:
        field = dict(row)
        field['required'] = bool(field.get('required'))
        field['nullable'] = bool(field.get('nullable'))
        field['signed'] = bool(field.get('signed'))
        field['visible_floor'] = bool(field.get('visible_floor'))
        field['visible_dashboard'] = bool(field.get('visible_dashboard'))
        field['default_value'] = profile_json_load(field.pop(
            'default_value_json', None), None)
        fields.append(field)
    profile['fields'] = fields
    rule_rows = conn.execute(
        """
        SELECT *
        FROM sensor_profile_rules
        WHERE profile_id = ?
        ORDER BY
            CASE severity
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                WHEN 'info' THEN 3
                ELSE 4
            END,
            id
        """
        , (profile['id'],)).fetchall()
    rules = []
    for row in rule_rows:
        rule = dict(row)
        rule['enabled'] = bool(rule.get('enabled'))
        rule['auto_resolve'] = bool(rule.get('auto_resolve'))
        if rule.get('expected_boolean') is not None:
            rule['expected_boolean'] = bool(rule['expected_boolean'])
        rules.append(rule)
    profile['rules'] = rules
    sensor_rows = conn.execute(
        """
        SELECT
            relation.id AS relation_id,
            relation.role,
            relation.required,
            relation.configuration_json,
            relation.display_order,

            catalog.id AS sensor_id,
            catalog.sensor_code,
            catalog.manufacturer,
            catalog.model,
            catalog.use_case,
            catalog.protocol AS sensor_protocol,
            catalog.default_bus,
            catalog.default_address,
            catalog.datasheet_url,
            catalog.description AS sensor_description,
            catalog.enabled AS sensor_enabled,

            module.id AS firmware_module_id,
            module.module_key,
            module.display_name AS module_display_name,
            module.driver_class,
            module.protocol AS module_protocol,
            module.library_name,
            module.library_version,
            module.supported_board,
            module.min_firmware_version,
            module.source_file,
            module.notes AS module_notes,
            module.enabled AS module_enabled

        FROM sensor_profile_sensors relation

        INNER JOIN sensor_catalog catalog
            ON catalog.id = relation.sensor_id

        LEFT JOIN firmware_modules module
            ON module.id = relation.firmware_module_id

        WHERE relation.profile_id = ?

        ORDER BY relation.display_order, relation.id
        """
        , (profile['id'],)).fetchall()
    sensors = []
    for row in sensor_rows:
        sensor = dict(row)
        sensor['required'] = bool(sensor.get('required'))
        sensor['sensor_enabled'] = bool(sensor.get('sensor_enabled'))
        if sensor.get('module_enabled') is not None:
            sensor['module_enabled'] = bool(sensor['module_enabled'])
        sensor['configuration'] = profile_json_load(sensor.pop(
            'configuration_json', None), {})
        sensors.append(sensor)
    profile['sensors'] = sensors
    compatibility_rows = conn.execute(
        """
        SELECT
            compatibility.id,
            compatibility.profile_id,
            compatibility.firmware_module_id,
            compatibility.min_firmware_version,
            compatibility.max_firmware_version,
            compatibility.hardware_revision,
            compatibility.required_features_json,
            compatibility.enabled,

            module.module_key,
            module.display_name,
            module.driver_class,
            module.supported_board

        FROM profile_firmware_compatibility compatibility

        INNER JOIN firmware_modules module
            ON module.id = compatibility.firmware_module_id

        WHERE compatibility.profile_id = ?

        ORDER BY module.display_name
        """
        , (profile['id'],)).fetchall()
    compatibility = []
    for row in compatibility_rows:
        item = dict(row)
        item['enabled'] = bool(item.get('enabled'))
        item['required_features'] = profile_json_load(item.pop(
            'required_features_json', None), [])
        compatibility.append(item)
    profile['firmware_compatibility'] = compatibility
    version_rows = conn.execute(
        """
        SELECT
            id,
            version,
            change_note,
            created_by,
            created_at
        FROM sensor_profile_versions
        WHERE profile_id = ?
        ORDER BY version DESC
        """
        , (profile['id'],)).fetchall()
    profile['versions'] = [dict(row) for row in version_rows]
    device_rows = conn.execute(
        """
        SELECT
            device_id,
            chip_mac,
            label,
            node_type,
            building,
            floor,
            room,
            profile_code,
            profile_version,
            configuration_status,
            configuration_error
        FROM devices
        WHERE profile_id = ?
           OR (
                profile_id IS NULL
                AND profile_code = ?
           )
        ORDER BY label, device_id
        """
        , (profile['id'], profile['profile_code'])).fetchall()
    profile['assigned_devices'] = [dict(row) for row in device_rows]
    profile['device_count'] = len(profile['assigned_devices'])
    profile['can_delete'] = not profile['is_system'] and profile['device_count'
        ] == 0
    return profile


def firmware_version_tuple(value: (str | None)):
    """
    Convert a semantic firmware version such as 1.0.0 into a tuple
    that can be safely compared.

    Pre-release text after a dash is ignored:
        1.2.0-beta -> (1, 2, 0)
    """
    clean_value = str(value or '').strip()
    if not clean_value:
        return None
    clean_value = clean_value.split('-', 1)[0]
    parts = clean_value.split('.')
    if not 1 <= len(parts) <= 4:
        return None
    normalized_parts = []
    for part in parts:
        if not part.isdigit():
            return None
        normalized_parts.append(int(part))
    while len(normalized_parts) < 4:
        normalized_parts.append(0)
    return tuple(normalized_parts)


def firmware_version_is_compatible(current_version: str, minimum_version: (
    str | None), maximum_version: (str | None)):
    """
    Check whether one firmware version falls within an optional
    inclusive minimum and maximum range.
    """
    current_tuple = firmware_version_tuple(current_version)
    if current_tuple is None:
        return False
    if minimum_version:
        minimum_tuple = firmware_version_tuple(minimum_version)
        if minimum_tuple is None:
            return False
        if current_tuple < minimum_tuple:
            return False
    if maximum_version:
        maximum_tuple = firmware_version_tuple(maximum_version)
        if maximum_tuple is None:
            return False
        if current_tuple > maximum_tuple:
            return False
    return True


def resolve_sensor_profile_for_provision(conn: sqlite3.Connection, device:
    Device):
    """
    Validate the LILYGO profile selection against the database and
    return backend-controlled canonical values.

    Browser-submitted values are never treated as the source of truth.
    """
    if device.profile_id is None:
        raise HTTPException(status_code=400, detail=
            'profile_id is required for profile-driven provisioning')
    submitted_profile_code = str(device.profile_code or '').strip().upper()
    if not submitted_profile_code:
        raise HTTPException(status_code=400, detail=
            'profile_code is required for profile-driven provisioning')
    profile_row = get_sensor_profile_row(conn, device.profile_id)
    if not profile_row:
        raise HTTPException(status_code=404, detail=
            f'Sensor profile not found: {device.profile_id}')
    profile = dict(profile_row)
    stored_profile_code = str(profile.get('profile_code') or '').strip().upper(
        )
    if submitted_profile_code != stored_profile_code:
        raise HTTPException(status_code=409, detail={'message':
            'profile_id and profile_code do not match',
            'submitted_profile_id': device.profile_id,
            'submitted_profile_code': submitted_profile_code,
            'stored_profile_code': stored_profile_code})
    if not bool(profile.get('enabled')):
        raise HTTPException(status_code=409, detail=
            f'Sensor profile is disabled: {stored_profile_code}')
    if str(profile.get('status') or '').strip().lower() != 'active':
        raise HTTPException(status_code=409, detail=
            f'Sensor profile must be active before provisioning: {stored_profile_code}'
            )
    stored_profile_version = int(profile.get('profile_version') or 0)
    stored_payload_version = int(profile.get('payload_version') or 0)
    if device.profile_version != stored_profile_version:
        raise HTTPException(status_code=409, detail={'message':
            'Profile version is outdated or invalid', 'submitted': device.
            profile_version, 'current': stored_profile_version})
    if device.payload_version != stored_payload_version:
        raise HTTPException(status_code=409, detail={'message':
            'Payload version does not match the profile', 'submitted':
            device.payload_version, 'current': stored_payload_version})
    stored_encoder_key = str(profile.get('payload_encoder_key') or '').strip()
    submitted_encoder_key = str(device.payload_encoder_key or '').strip()
    if submitted_encoder_key != stored_encoder_key:
        raise HTTPException(status_code=409, detail={'message':
            'Payload encoder does not match the profile', 'submitted':
            submitted_encoder_key, 'current': stored_encoder_key})
    stored_interval = int(profile.get('uplink_interval_seconds') or 0)
    if device.uplink_interval_seconds != stored_interval:
        raise HTTPException(status_code=409, detail={'message':
            'Uplink interval does not match the selected profile',
            'submitted': device.uplink_interval_seconds, 'current':
            stored_interval})
    stored_node_type = str(profile.get('node_type') or '').strip().lower()
    submitted_node_type = str(device.node_type or '').strip().lower()
    if submitted_node_type != stored_node_type:
        raise HTTPException(status_code=409, detail={'message':
            'node_type does not match the sensor profile', 'submitted':
            submitted_node_type, 'current': stored_node_type})
    firmware_version = str(device.firmware_version or '').strip()
    if firmware_version_tuple(firmware_version) is None:
        raise HTTPException(status_code=400, detail=
            'firmware_version is required and must use a numeric version such as 1.0.0'
            )
    compatibility_rows = conn.execute(
        """
        SELECT
            compatibility.min_firmware_version,
            compatibility.max_firmware_version,
            module.module_key,
            module.display_name
        FROM profile_firmware_compatibility compatibility
        INNER JOIN firmware_modules module
            ON module.id = compatibility.firmware_module_id
        WHERE compatibility.profile_id = ?
          AND compatibility.enabled = 1
          AND module.enabled = 1
        ORDER BY module.module_key
        """
        , (profile['id'],)).fetchall()
    incompatibilities = []
    for row in compatibility_rows:
        if not firmware_version_is_compatible(firmware_version, row[
            'min_firmware_version'], row['max_firmware_version']):
            incompatibilities.append({'module_key': row['module_key'],
                'module_name': row['display_name'], 'minimum': row[
                'min_firmware_version'], 'maximum': row[
                'max_firmware_version'], 'current': firmware_version})
    if incompatibilities:
        raise HTTPException(status_code=409, detail={'message':
            'Firmware is not compatible with the selected profile',
            'incompatibilities': incompatibilities})
    capabilities = profile_json_load(profile.get('capabilities_json'), [])
    return {'profile_id': int(profile['id']), 'profile_code':
        stored_profile_code, 'profile_name': profile.get('profile_name'),
        'profile_version': stored_profile_version, 'node_type':
        stored_node_type, 'capabilities': capabilities, 'payload_version':
        stored_payload_version, 'payload_encoder_key': stored_encoder_key,
        'f_port': int(profile.get('f_port') or 1),
        'uplink_interval_seconds': stored_interval, 'firmware_version':
        firmware_version, 'configuration_status': 'validated',
        'configuration_checksum': profile.get('schema_checksum'),
        'icon_type': profile.get('icon_type') or infer_icon_type(
        stored_node_type), 'tb_device_profile_name': profile.get(
        'tb_device_profile_name'), 'ttn_formatter_type': profile.get(
        'ttn_formatter_type'), 'ttn_formatter_code': profile.get(
        'ttn_formatter_code')}


def validate_sensor_profile_definition(conn: sqlite3.Connection, data: dict,
    exclude_profile_id: (int | None)=None):
    """
    Validate and normalize a complete sensor-profile definition.

    Returns:
        {
            "valid": bool,
            "errors": list[str],
            "normalized": dict | None,
            "schema_checksum": str | None
        }
    """
    errors = []
    if not isinstance(data, dict):
        return {'valid': False, 'errors': [
            'Profile definition must be a JSON object'], 'normalized': None,
            'schema_checksum': None}
    profile_code = str(data.get('profile_code') or '').strip().upper()
    profile_name = str(data.get('profile_name') or '').strip()
    node_type = str(data.get('node_type') or '').strip().lower()
    description = str(data.get('description') or '').strip()
    if not PROFILE_CODE_PATTERN.fullmatch(profile_code):
        errors.append(
            'profile_code must start with an uppercase letter and contain only A-Z, 0-9 and underscores'
            )
    if not profile_name:
        errors.append('profile_name is required')
    if len(profile_name) > 120:
        errors.append('profile_name must not exceed 120 characters')
    if not PROFILE_KEY_PATTERN.fullmatch(node_type):
        errors.append(
            'node_type must start with a lowercase letter and contain only lowercase letters, numbers and underscores'
            )
    existing_profile = None
    if profile_code:
        if exclude_profile_id is None:
            existing_profile = conn.execute(
                """
                SELECT id
                FROM sensor_profiles
                WHERE UPPER(profile_code) = UPPER(?)
                LIMIT 1
                """
                , (profile_code,)).fetchone()
        else:
            existing_profile = conn.execute(
                """
                SELECT id
                FROM sensor_profiles
                WHERE UPPER(profile_code) = UPPER(?)
                  AND id != ?
                LIMIT 1
                """
                , (profile_code, exclude_profile_id)).fetchone()
    if existing_profile:
        errors.append(f'profile_code already exists: {profile_code}')
    capabilities = profile_unique_string_list(data.get('capabilities', []))
    if not capabilities:
        errors.append('At least one capability is required')
    for capability in capabilities:
        if not PROFILE_KEY_PATTERN.fullmatch(capability):
            errors.append(f'Invalid capability key: {capability}')
    configuration_schema = data.get('configuration_schema', {})
    if configuration_schema is None:
        configuration_schema = {}
    if not isinstance(configuration_schema, dict):
        errors.append('configuration_schema must be a JSON object')
        configuration_schema = {}
    payload_encoder_key = str(data.get('payload_encoder_key') or '').strip(
        ).lower()
    if not PROFILE_KEY_PATTERN.fullmatch(payload_encoder_key):
        errors.append(
            'payload_encoder_key must contain only lowercase letters, numbers and underscores'
            )
    profile_version = profile_optional_integer(data.get('profile_version', 
        1), 'profile_version', errors, minimum=1, maximum=9999)
    payload_version = profile_optional_integer(data.get('payload_version', 
        1), 'payload_version', errors, minimum=1, maximum=9999)
    f_port = profile_optional_integer(data.get('f_port', 1), 'f_port',
        errors, minimum=1, maximum=223)
    uplink_interval_seconds = profile_optional_integer(data.get(
        'uplink_interval_seconds', 60), 'uplink_interval_seconds', errors,
        minimum=5, maximum=86400)
    ttn_formatter_type = str(data.get('ttn_formatter_type') or 'javascript'
        ).strip().lower()
    ttn_formatter_code = str(data.get('ttn_formatter_code') or '').strip()
    if ttn_formatter_type != 'javascript':
        errors.append('Only JavaScript TTN formatters are currently supported')
    if not ttn_formatter_code:
        errors.append('ttn_formatter_code is required')
    elif 'decodeUplink' not in ttn_formatter_code:
        errors.append('TTN formatter must contain a decodeUplink function')
    tb_device_profile_name = str(data.get('tb_device_profile_name') or ''
        ).strip()
    icon_type = str(data.get('icon_type') or 'default').strip().lower()
    icon_color = str(data.get('icon_color') or '').strip()
    status = str(data.get('status') or 'draft').strip().lower()
    if status not in PROFILE_ALLOWED_STATUSES:
        errors.append('status must be one of: ' + ', '.join(sorted(
            PROFILE_ALLOWED_STATUSES)))
    enabled = profile_value_to_boolean(data.get('enabled', True), default=True)
    raw_fields = data.get('fields', [])
    if not isinstance(raw_fields, list):
        errors.append('fields must be a list')
        raw_fields = []
    if not raw_fields:
        errors.append('At least one telemetry field is required')
    normalized_fields = []
    field_keys = set()
    for index, raw_field in enumerate(raw_fields):
        field_prefix = f'fields[{index}]'
        if not isinstance(raw_field, dict):
            errors.append(f'{field_prefix} must be an object')
            continue
        field_key = str(raw_field.get('field_key') or '').strip().lower()
        label = str(raw_field.get('label') or '').strip()
        unit = raw_field.get('unit')
        if unit is not None:
            unit = str(unit).strip() or None
        data_type = str(raw_field.get('data_type') or 'number').strip().lower()
        if not PROFILE_KEY_PATTERN.fullmatch(field_key):
            errors.append(f'{field_prefix}.field_key is invalid')
        if field_key in field_keys:
            errors.append(f'Duplicate field_key: {field_key}')
        field_keys.add(field_key)
        if not label:
            errors.append(f'{field_prefix}.label is required')
        if data_type not in PROFILE_ALLOWED_DATA_TYPES:
            errors.append(f'{field_prefix}.data_type must be one of: ' +
                ', '.join(sorted(PROFILE_ALLOWED_DATA_TYPES)))
        payload_order = profile_optional_integer(raw_field.get(
            'payload_order', index + 1), f'{field_prefix}.payload_order',
            errors, minimum=1)
        byte_offset = profile_optional_integer(raw_field.get('byte_offset'),
            f'{field_prefix}.byte_offset', errors, minimum=0)
        byte_length = profile_optional_integer(raw_field.get('byte_length'),
            f'{field_prefix}.byte_length', errors, minimum=1)
        scale = profile_optional_float(raw_field.get('scale', 1),
            f'{field_prefix}.scale', errors)
        if scale is None:
            scale = 1.0
        display_order = profile_optional_integer(raw_field.get(
            'display_order', index + 1), f'{field_prefix}.display_order',
            errors, minimum=0)
        precision_digits = profile_optional_integer(raw_field.get(
            'precision_digits'), f'{field_prefix}.precision_digits', errors,
            minimum=0, maximum=8)
        min_value = profile_optional_float(raw_field.get('min_value'),
            f'{field_prefix}.min_value', errors)
        max_value = profile_optional_float(raw_field.get('max_value'),
            f'{field_prefix}.max_value', errors)
        if (min_value is not None and max_value is not None and min_value >
            max_value):
            errors.append(f'{field_prefix}.min_value cannot exceed max_value')
        endianness = str(raw_field.get('endianness') or 'big').strip().lower()
        if endianness not in {'big', 'little'}:
            errors.append(f'{field_prefix}.endianness must be big or little')
        normalized_fields.append({'field_key': field_key, 'label': label,
            'unit': unit, 'data_type': data_type, 'payload_order':
            payload_order, 'byte_offset': byte_offset, 'byte_length':
            byte_length, 'scale': scale, 'signed': profile_value_to_boolean
            (raw_field.get('signed', False)), 'endianness': endianness,
            'required': profile_value_to_boolean(raw_field.get('required', 
            True)), 'nullable': profile_value_to_boolean(raw_field.get(
            'nullable', False)), 'display_order': display_order,
            'precision_digits': precision_digits, 'visible_floor':
            profile_value_to_boolean(raw_field.get('visible_floor', True)),
            'visible_dashboard': profile_value_to_boolean(raw_field.get(
            'visible_dashboard', True)), 'min_value': min_value,
            'max_value': max_value, 'default_value': raw_field.get(
            'default_value')})
    raw_sensors = data.get('sensors', [])
    if not isinstance(raw_sensors, list):
        errors.append('sensors must be a list')
        raw_sensors = []
    if not raw_sensors:
        errors.append(
            'At least one physical or compatibility sensor component is required'
            )
    normalized_sensors = []
    sensor_relationship_keys = set()
    for index, raw_sensor in enumerate(raw_sensors):
        sensor_prefix = f'sensors[{index}]'
        if not isinstance(raw_sensor, dict):
            errors.append(f'{sensor_prefix} must be an object')
            continue
        sensor_code = str(raw_sensor.get('sensor_code') or '').strip().upper()
        module_key = str(raw_sensor.get('module_key') or '').strip().lower()
        role = str(raw_sensor.get('role') or 'primary').strip().lower()
        if not sensor_code:
            errors.append(f'{sensor_prefix}.sensor_code is required')
        if not PROFILE_KEY_PATTERN.fullmatch(module_key):
            errors.append(f'{sensor_prefix}.module_key is invalid')
        if not PROFILE_KEY_PATTERN.fullmatch(role):
            errors.append(f'{sensor_prefix}.role is invalid')
        relationship_key = sensor_code, role
        if relationship_key in sensor_relationship_keys:
            errors.append(
                f'Duplicate sensor and role combination: {sensor_code}/{role}')
        sensor_relationship_keys.add(relationship_key)
        sensor_row = conn.execute(
            """
            SELECT id
            FROM sensor_catalog
            WHERE sensor_code = ?
            LIMIT 1
            """
            , (sensor_code,)).fetchone()
        if not sensor_row:
            errors.append(f'Unknown sensor_code: {sensor_code}')
        module_row = conn.execute(
            """
            SELECT id
            FROM firmware_modules
            WHERE module_key = ?
            LIMIT 1
            """
            , (module_key,)).fetchone()
        if not module_row:
            errors.append(f'Unknown firmware module: {module_key}')
        configuration = raw_sensor.get('configuration', {})
        if configuration is None:
            configuration = {}
        if not isinstance(configuration, dict):
            errors.append(f'{sensor_prefix}.configuration must be an object')
            configuration = {}
        normalized_sensors.append({'sensor_code': sensor_code, 'module_key':
            module_key, 'role': role, 'required': profile_value_to_boolean(
            raw_sensor.get('required', True)), 'configuration':
            configuration, 'display_order': profile_optional_integer(
            raw_sensor.get('display_order', index + 1),
            f'{sensor_prefix}.display_order', errors, minimum=0)})
    raw_rules = data.get('rules', [])
    if not isinstance(raw_rules, list):
        errors.append('rules must be a list')
        raw_rules = []
    normalized_rules = []
    rule_codes = set()
    for index, raw_rule in enumerate(raw_rules):
        rule_prefix = f'rules[{index}]'
        if not isinstance(raw_rule, dict):
            errors.append(f'{rule_prefix} must be an object')
            continue
        rule_code = str(raw_rule.get('rule_code') or '').strip().upper()
        field_key = str(raw_rule.get('field_key') or '').strip().lower()
        operator = str(raw_rule.get('operator') or '').strip().lower()
        severity = str(raw_rule.get('severity') or 'warning').strip().lower()
        alarm_type = str(raw_rule.get('alarm_type') or node_type).strip(
            ).lower()
        message_template = str(raw_rule.get('message_template') or '').strip()
        if not PROFILE_CODE_PATTERN.fullmatch(rule_code):
            errors.append(f'{rule_prefix}.rule_code is invalid')
        if rule_code in rule_codes:
            errors.append(f'Duplicate rule_code: {rule_code}')
        rule_codes.add(rule_code)
        if field_key not in field_keys:
            errors.append(
                f'{rule_prefix}.field_key does not exist in telemetry fields: {field_key}'
                )
        if operator not in PROFILE_ALLOWED_OPERATORS:
            errors.append(f'{rule_prefix}.operator must be one of: ' + ', '
                .join(sorted(PROFILE_ALLOWED_OPERATORS)))
        if severity not in PROFILE_ALLOWED_SEVERITIES:
            errors.append(f'{rule_prefix}.severity must be one of: ' + ', '
                .join(sorted(PROFILE_ALLOWED_SEVERITIES)))
        if not PROFILE_KEY_PATTERN.fullmatch(alarm_type):
            errors.append(f'{rule_prefix}.alarm_type is invalid')
        if not message_template:
            errors.append(f'{rule_prefix}.message_template is required')
        threshold_value = profile_optional_float(raw_rule.get(
            'threshold_value'), f'{rule_prefix}.threshold_value', errors)
        threshold_value_2 = profile_optional_float(raw_rule.get(
            'threshold_value_2'), f'{rule_prefix}.threshold_value_2', errors)
        expected_boolean = raw_rule.get('expected_boolean')
        if expected_boolean is not None:
            expected_boolean = profile_value_to_boolean(expected_boolean)
        expected_text = raw_rule.get('expected_text')
        if expected_text is not None:
            expected_text = str(expected_text)
        if operator in {'>', '>=', '<', '<='}:
            if threshold_value is None:
                errors.append(
                    f'{rule_prefix}.threshold_value is required for operator {operator}'
                    )
        if operator in {'between', 'outside'}:
            if threshold_value is None or threshold_value_2 is None:
                errors.append(f'{rule_prefix} requires two threshold values')
            elif threshold_value > threshold_value_2:
                errors.append(
                    f'{rule_prefix}.threshold_value cannot exceed threshold_value_2'
                    )
        if operator in {'==', '!='}:
            if (threshold_value is None and expected_boolean is None and 
                expected_text is None):
                errors.append(
                    f'{rule_prefix} requires a threshold, Boolean or text comparison value'
                    )
        if operator == 'contains' and expected_text is None:
            errors.append(
                f'{rule_prefix}.expected_text is required for contains')
        normalized_rules.append({'rule_code': rule_code, 'field_key':
            field_key, 'operator': operator, 'threshold_value':
            threshold_value, 'threshold_value_2': threshold_value_2,
            'expected_boolean': expected_boolean, 'expected_text':
            expected_text, 'severity': severity, 'alarm_type': alarm_type,
            'message_template': message_template, 'debounce_seconds':
            profile_optional_integer(raw_rule.get('debounce_seconds', 0),
            f'{rule_prefix}.debounce_seconds', errors, minimum=0, maximum=
            86400), 'cooldown_seconds': profile_optional_integer(raw_rule.
            get('cooldown_seconds', 300), f'{rule_prefix}.cooldown_seconds',
            errors, minimum=0, maximum=604800), 'auto_resolve':
            profile_value_to_boolean(raw_rule.get('auto_resolve', True)),
            'enabled': profile_value_to_boolean(raw_rule.get('enabled', True))}
            )
    normalized = {'profile_code': profile_code, 'profile_name':
        profile_name, 'profile_version': profile_version, 'node_type':
        node_type, 'description': description, 'capabilities': capabilities,
        'configuration_schema': configuration_schema, 'payload_encoder_key':
        payload_encoder_key, 'payload_version': payload_version, 'f_port':
        f_port, 'uplink_interval_seconds': uplink_interval_seconds,
        'ttn_formatter_code': ttn_formatter_code, 'ttn_formatter_type':
        ttn_formatter_type, 'tb_device_profile_name': 
        tb_device_profile_name or None, 'icon_type': icon_type,
        'icon_color': icon_color or None, 'status': status, 'enabled':
        enabled, 'fields': normalized_fields, 'rules': normalized_rules,
        'sensors': normalized_sensors}
    schema_checksum = None
    if not errors:
        schema_checksum = calculate_sensor_profile_checksum(normalized)
    return {'valid': len(errors) == 0, 'errors': errors, 'normalized':
        normalized, 'schema_checksum': schema_checksum}


def profile_utc_now_iso():
    """
    Return the current UTC time in ISO 8601 format.
    """
    return datetime.now(timezone.utc).isoformat()


def insert_sensor_profile_version_snapshot(conn: sqlite3.Connection,
    profile_id: int, version: int, normalized_definition: dict,
    schema_checksum: str, change_note: str, actor: str):
    """
    Store one immutable profile-version snapshot.
    """
    snapshot = {'profile_id': profile_id, 'version': version,
        'schema_checksum': schema_checksum, 'definition': normalized_definition
        }
    conn.execute(
        """
        INSERT INTO sensor_profile_versions(
            profile_id,
            version,
            snapshot_json,
            change_note,
            created_by,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """
        , (profile_id, version, json.dumps(snapshot, ensure_ascii=False,
        sort_keys=True), change_note, actor, profile_utc_now_iso()))


def replace_sensor_profile_children(conn: sqlite3.Connection, profile_id:
    int, normalized_definition: dict):
    """
    Replace the profile's telemetry fields, alarm rules,
    sensor relationships and firmware compatibility records.

    This function does not commit the transaction.
    """
    conn.execute(
        """
        DELETE FROM profile_firmware_compatibility
        WHERE profile_id = ?
        """
        , (profile_id,))
    conn.execute(
        """
        DELETE FROM sensor_profile_sensors
        WHERE profile_id = ?
        """
        , (profile_id,))
    conn.execute(
        """
        DELETE FROM sensor_profile_fields
        WHERE profile_id = ?
        """
        , (profile_id,))
    conn.execute(
        """
        DELETE FROM sensor_profile_rules
        WHERE profile_id = ?
        """
        , (profile_id,))
    compatibility_modules = set()
    for component in normalized_definition['sensors']:
        sensor_row = conn.execute(
            """
            SELECT id
            FROM sensor_catalog
            WHERE sensor_code = ?
              AND enabled = 1
            LIMIT 1
            """
            , (component['sensor_code'],)).fetchone()
        if not sensor_row:
            raise ValueError('Sensor is missing or disabled: ' + component[
                'sensor_code'])
        module_row = conn.execute(
            """
            SELECT
                id,
                min_firmware_version
            FROM firmware_modules
            WHERE module_key = ?
              AND enabled = 1
            LIMIT 1
            """
            , (component['module_key'],)).fetchone()
        if not module_row:
            raise ValueError('Firmware module is missing or disabled: ' +
                component['module_key'])
        conn.execute(
            """
            INSERT INTO sensor_profile_sensors(
                profile_id,
                sensor_id,
                firmware_module_id,
                role,
                required,
                configuration_json,
                display_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            , (profile_id, sensor_row['id'], module_row['id'], component[
            'role'], int(component['required']), json.dumps(component[
            'configuration'], ensure_ascii=False, sort_keys=True),
            component['display_order']))
        firmware_module_id = module_row['id']
        if firmware_module_id in compatibility_modules:
            continue
        compatibility_modules.add(firmware_module_id)
        required_features = [normalized_definition['payload_encoder_key'], 
            'payload_v' + str(normalized_definition['payload_version'])]
        conn.execute(
            """
            INSERT INTO profile_firmware_compatibility(
                profile_id,
                firmware_module_id,
                min_firmware_version,
                max_firmware_version,
                hardware_revision,
                required_features_json,
                enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, 1)
            """
            , (profile_id, firmware_module_id, module_row[
            'min_firmware_version'], None, None, json.dumps(required_features))
            )
    for field in normalized_definition['fields']:
        conn.execute(
            """
            INSERT INTO sensor_profile_fields(
                profile_id,
                field_key,
                label,
                unit,
                data_type,

                payload_order,
                byte_offset,
                byte_length,
                scale,
                signed,
                endianness,

                required,
                nullable,

                display_order,
                precision_digits,

                visible_floor,
                visible_dashboard,

                min_value,
                max_value,
                default_value_json,

                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?
            )
            """
            , (profile_id, field['field_key'], field['label'], field['unit'
            ], field['data_type'], field['payload_order'], field[
            'byte_offset'], field['byte_length'], field['scale'], int(field
            ['signed']), field['endianness'], int(field['required']), int(
            field['nullable']), field['display_order'], field[
            'precision_digits'], int(field['visible_floor']), int(field[
            'visible_dashboard']), field['min_value'], field['max_value'],
            json.dumps(field['default_value'], ensure_ascii=False),
            profile_utc_now_iso(), profile_utc_now_iso()))
    for rule in normalized_definition['rules']:
        expected_boolean = rule['expected_boolean']
        if expected_boolean is not None:
            expected_boolean = int(expected_boolean)
        conn.execute(
            """
            INSERT INTO sensor_profile_rules(
                profile_id,
                rule_code,
                field_key,
                operator,

                threshold_value,
                threshold_value_2,
                expected_boolean,
                expected_text,

                severity,
                alarm_type,
                message_template,

                debounce_seconds,
                cooldown_seconds,
                auto_resolve,
                enabled,

                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            """
            , (profile_id, rule['rule_code'], rule['field_key'], rule[
            'operator'], rule['threshold_value'], rule['threshold_value_2'],
            expected_boolean, rule['expected_text'], rule['severity'], rule
            ['alarm_type'], rule['message_template'], rule[
            'debounce_seconds'], rule['cooldown_seconds'], int(rule[
            'auto_resolve']), int(rule['enabled']), profile_utc_now_iso(),
            profile_utc_now_iso()))


def create_sensor_profile_record(conn: sqlite3.Connection,
    profile_definition: dict, actor: str='admin', change_note: str=
    'Profile created'):
    """
    Validate and create a new non-system sensor profile.

    This function does not commit the transaction.
    """
    definition = dict(profile_definition or {})
    definition['profile_version'] = 1
    definition.setdefault('payload_version', 1)
    definition.setdefault('status', 'draft')
    definition.setdefault('enabled', True)
    validation = validate_sensor_profile_definition(conn, definition)
    if not validation['valid']:
        raise ValueError('Profile validation failed: ' + '; '.join(
            validation['errors']))
    normalized = validation['normalized']
    schema_checksum = validation['schema_checksum']
    now = profile_utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO sensor_profiles(
            profile_code,
            profile_name,
            profile_version,
            node_type,
            description,

            capabilities_json,
            configuration_schema_json,

            payload_encoder_key,
            payload_version,
            f_port,
            uplink_interval_seconds,

            ttn_formatter_code,
            ttn_formatter_type,

            tb_device_profile_name,

            icon_type,
            icon_color,

            status,
            enabled,
            is_system,

            schema_checksum,
            created_by,
            updated_by,
            created_at,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?, ?, 0,
            ?, ?, ?, ?, ?
        )
        """
        , (normalized['profile_code'], normalized['profile_name'],
        normalized['profile_version'], normalized['node_type'], normalized[
        'description'], json.dumps(normalized['capabilities'], ensure_ascii
        =False), json.dumps(normalized['configuration_schema'],
        ensure_ascii=False, sort_keys=True), normalized[
        'payload_encoder_key'], normalized['payload_version'], normalized[
        'f_port'], normalized['uplink_interval_seconds'], normalized[
        'ttn_formatter_code'], normalized['ttn_formatter_type'], normalized
        ['tb_device_profile_name'], normalized['icon_type'], normalized[
        'icon_color'], normalized['status'], int(normalized['enabled']),
        schema_checksum, actor, actor, now, now))
    profile_id = cursor.lastrowid
    replace_sensor_profile_children(conn, profile_id, normalized)
    insert_sensor_profile_version_snapshot(conn, profile_id, normalized[
        'profile_version'], normalized, schema_checksum, change_note, actor)
    return get_sensor_profile_detail(conn, profile_id, include_formatter=True)


def update_sensor_profile_record(conn: sqlite3.Connection, profile_id: int,
    profile_definition: dict, actor: str='admin', change_note: str=
    'Profile updated'):
    """
    Update a custom profile and create a new profile version.

    Seeded system profiles are immutable and must be cloned first.
    This function does not commit the transaction.
    """
    existing = get_sensor_profile_detail(conn, profile_id,
        include_formatter=True)
    if not existing:
        raise ValueError('Sensor profile not found: ' + str(profile_id))
    if existing['is_system']:
        raise ValueError(
            'System profiles cannot be edited directly. Clone the profile and edit the clone.'
            )
    supplied_code = profile_definition.get('profile_code')
    if supplied_code is not None and str(supplied_code).strip().upper(
        ) != existing['profile_code']:
        raise ValueError('profile_code cannot be changed after creation')
    merged_definition = {'profile_code': existing['profile_code'],
        'profile_name': existing['profile_name'], 'profile_version':
        existing['profile_version'], 'node_type': existing['node_type'],
        'description': existing['description'], 'capabilities': existing[
        'capabilities'], 'configuration_schema': existing[
        'configuration_schema'], 'payload_encoder_key': existing[
        'payload_encoder_key'], 'payload_version': existing[
        'payload_version'], 'f_port': existing['f_port'],
        'uplink_interval_seconds': existing['uplink_interval_seconds'],
        'ttn_formatter_code': existing['ttn_formatter_code'],
        'ttn_formatter_type': existing['ttn_formatter_type'],
        'tb_device_profile_name': existing['tb_device_profile_name'],
        'icon_type': existing['icon_type'], 'icon_color': existing[
        'icon_color'], 'status': existing['status'], 'enabled': existing[
        'enabled'], 'fields': existing['fields'], 'rules': existing['rules'
        ], 'sensors': existing['sensors']}
    merged_definition.update(dict(profile_definition or {}))
    next_version = int(existing['profile_version']) + 1
    merged_definition['profile_code'] = existing['profile_code']
    merged_definition['profile_version'] = next_version
    validation = validate_sensor_profile_definition(conn, merged_definition,
        exclude_profile_id=profile_id)
    if not validation['valid']:
        raise ValueError('Profile validation failed: ' + '; '.join(
            validation['errors']))
    normalized = validation['normalized']
    schema_checksum = validation['schema_checksum']
    now = profile_utc_now_iso()
    conn.execute(
        """
        UPDATE sensor_profiles
        SET
            profile_name = ?,
            profile_version = ?,
            node_type = ?,
            description = ?,

            capabilities_json = ?,
            configuration_schema_json = ?,

            payload_encoder_key = ?,
            payload_version = ?,
            f_port = ?,
            uplink_interval_seconds = ?,

            ttn_formatter_code = ?,
            ttn_formatter_type = ?,

            tb_device_profile_name = ?,

            icon_type = ?,
            icon_color = ?,

            status = ?,
            enabled = ?,

            schema_checksum = ?,
            updated_by = ?,
            updated_at = ?

        WHERE id = ?
        """
        , (normalized['profile_name'], normalized['profile_version'],
        normalized['node_type'], normalized['description'], json.dumps(
        normalized['capabilities'], ensure_ascii=False), json.dumps(
        normalized['configuration_schema'], ensure_ascii=False, sort_keys=
        True), normalized['payload_encoder_key'], normalized[
        'payload_version'], normalized['f_port'], normalized[
        'uplink_interval_seconds'], normalized['ttn_formatter_code'],
        normalized['ttn_formatter_type'], normalized[
        'tb_device_profile_name'], normalized['icon_type'], normalized[
        'icon_color'], normalized['status'], int(normalized['enabled']),
        schema_checksum, actor, now, profile_id))
    replace_sensor_profile_children(conn, profile_id, normalized)
    insert_sensor_profile_version_snapshot(conn, profile_id, normalized[
        'profile_version'], normalized, schema_checksum, change_note, actor)
    return get_sensor_profile_detail(conn, profile_id, include_formatter=True)


def generate_unique_sensor_profile_code(conn: sqlite3.Connection,
    profile_name: str):
    """
    Generate an internal profile code without requiring the
    administrator to enter technical identifiers.
    """
    clean_name = re.sub('[^A-Z0-9]+', '_', str(profile_name or '').strip().
        upper()).strip('_')
    if not clean_name:
        clean_name = 'SENSOR_PROFILE'
    if not clean_name[0].isalpha():
        clean_name = 'PROFILE_' + clean_name
    clean_name = clean_name[:52].rstrip('_')
    if len(clean_name) < 3:
        clean_name = 'SENSOR_PROFILE'
    base_code = (clean_name + '_V1')[:64].rstrip('_')
    candidate = base_code
    suffix_number = 2
    while conn.execute(
        """
        SELECT 1
        FROM sensor_profiles
        WHERE UPPER(profile_code) = UPPER(?)
        LIMIT 1
        """
        , (candidate,)).fetchone():
        suffix = '_' + str(suffix_number)
        candidate = base_code[:64 - len(suffix)].rstrip('_') + suffix
        suffix_number += 1
    return candidate


def clone_sensor_profile_record(conn: sqlite3.Connection,
    source_profile_identifier, new_profile_code: str, new_profile_name: str,
    actor: str='admin', activate: bool=False, uplink_interval_seconds: (int |
    None)=None, alarm_thresholds: (dict | None)=None):
    """
    Clone an existing sensor profile while preserving its complete
    technical configuration.

    Simple administrator creation can:
    - Generate the profile code automatically.
    - Activate the profile immediately.
    - Override the uplink interval.
    - Override numeric alarm thresholds.

    The existing advanced clone workflow remains compatible.

    This function does not commit the transaction.
    """
    source = get_sensor_profile_detail(conn, source_profile_identifier,
        include_formatter=True)
    if not source:
        raise ValueError('Source sensor profile was not found')
    clean_profile_name = str(new_profile_name or '').strip()
    clean_profile_code = str(new_profile_code or '').strip().upper()
    if not clean_profile_name:
        raise ValueError('New profile name is required')
    if not clean_profile_code:
        clean_profile_code = generate_unique_sensor_profile_code(conn,
            clean_profile_name)
    source_interval = int(source.get('uplink_interval_seconds') or 60)
    if uplink_interval_seconds in (None, ''):
        selected_interval = source_interval
    else:
        try:
            selected_interval = int(uplink_interval_seconds)
        except Exception:
            raise ValueError('Uplink interval must be an integer')
        if not 5 <= selected_interval <= 86400:
            raise ValueError(
                'Uplink interval must be between 5 and 86400 seconds')
    if activate:
        if not bool(source.get('enabled')):
            raise ValueError(
                'Disabled templates cannot be used for simple profile creation'
                )
        source_status = str(source.get('status') or '').strip().lower()
        if source_status != 'active':
            raise ValueError(
                'Only active templates can be used for simple profile creation'
                )
    if alarm_thresholds is None:
        alarm_thresholds = {}
    if not isinstance(alarm_thresholds, dict):
        raise ValueError('alarm_thresholds must be an object')
    copied_rules = []
    for source_rule in source.get('rules', []):
        rule = dict(source_rule)
        rule_code = str(rule.get('rule_code') or '').strip().upper()
        field_key = str(rule.get('field_key') or '').strip().lower()
        override = None
        if rule_code in alarm_thresholds:
            override = alarm_thresholds[rule_code]
        elif field_key in alarm_thresholds:
            override = alarm_thresholds[field_key]
        if override is not None:
            if not isinstance(override, dict):
                override = {'threshold_value': override}
            for threshold_key in ('threshold_value', 'threshold_value_2'):
                if threshold_key not in override:
                    continue
                raw_value = override.get(threshold_key)
                if raw_value in (None, ''):
                    continue
                try:
                    rule[threshold_key] = float(raw_value)
                except Exception:
                    raise ValueError(
                        f'{rule_code} {threshold_key} must be numeric')
        copied_rules.append(rule)
    clone_definition = {'profile_code': clean_profile_code, 'profile_name':
        clean_profile_name, 'profile_version': 1, 'node_type': source[
        'node_type'], 'description': (
        'Created from the supported template ' + source['profile_name'] +
        '. ' + str(source.get('description') or '')).strip(),
        'capabilities': source['capabilities'], 'configuration_schema':
        source['configuration_schema'], 'payload_encoder_key': source[
        'payload_encoder_key'], 'payload_version': source['payload_version'
        ], 'f_port': source['f_port'], 'uplink_interval_seconds':
        selected_interval, 'ttn_formatter_code': source[
        'ttn_formatter_code'], 'ttn_formatter_type': source[
        'ttn_formatter_type'], 'tb_device_profile_name': source[
        'tb_device_profile_name'], 'icon_type': source['icon_type'],
        'icon_color': source['icon_color'], 'status': 'active' if activate else
        'draft', 'enabled': True, 'fields': source['fields'], 'rules':
        copied_rules, 'sensors': source['sensors']}
    change_note = 'Created from supported template ' + source['profile_code'
        ] if activate else 'Cloned from profile ' + source['profile_code']
    return create_sensor_profile_record(conn, clone_definition, actor=actor,
        change_note=change_note)


def set_sensor_profile_enabled(conn: sqlite3.Connection, profile_identifier,
    enabled: bool, actor: str='admin'):
    """
    Enable or disable a sensor profile.

    This function does not create a new schema version because
    the profile structure is not being changed.
    """
    profile = get_sensor_profile_detail(conn, profile_identifier,
        include_formatter=True)
    if not profile:
        raise ValueError('Sensor profile was not found')
    conn.execute(
        """
        UPDATE sensor_profiles
        SET
            enabled = ?,
            updated_by = ?,
            updated_at = ?
        WHERE id = ?
        """
        , (int(bool(enabled)), actor, profile_utc_now_iso(), profile['id']))
    return get_sensor_profile_detail(conn, profile['id'], include_formatter
        =True)


def delete_sensor_profile_record(conn: sqlite3.Connection, profile_identifier):
    """
    Delete an unused custom profile.

    System profiles, assigned profiles and profiles referenced
    by configuration history cannot be deleted.
    """
    profile = get_sensor_profile_detail(conn, profile_identifier,
        include_formatter=False)
    if not profile:
        raise ValueError('Sensor profile was not found')
    if profile['is_system']:
        raise ValueError('System profiles cannot be deleted')
    assigned_device_count = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM devices
        WHERE profile_id = ?
           OR profile_code = ?
        """
        , (profile['id'], profile['profile_code'])).fetchone()['total']
    if assigned_device_count > 0:
        raise ValueError(
            'Profile cannot be deleted because it is assigned to one or more devices'
            )
    configuration_history_count = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM device_configuration_history
        WHERE profile_id = ?
           OR profile_code = ?
        """
        , (profile['id'], profile['profile_code'])).fetchone()['total']
    if configuration_history_count > 0:
        raise ValueError(
            'Profile cannot be deleted because device configuration history references it. Disable or archive the profile instead.'
            )
    deleted_result = {'id': profile['id'], 'profile_code': profile[
        'profile_code'], 'profile_name': profile['profile_name']}
    conn.execute(
        """
        DELETE FROM sensor_profiles
        WHERE id = ?
        """
        , (profile['id'],))
    return deleted_result


ROOMS = {'Siemenshalle': {'0': {'Main Hall': {'x': 520, 'y': 430,
    'description': 'Large central open hall'}, 'Upper Corridor': {'x': 520,
    'y': 160, 'description': 'Long upper corridor area'}, 'Office 1': {'x':
    210, 'y': 145, 'description': 'Upper-left large room'}, 'Office 2': {
    'x': 335, 'y': 145, 'description': 'Upper-left small room group'},
    'Office 3': {'x': 480, 'y': 145, 'description': 'Upper middle room'},
    'Office 4': {'x': 650, 'y': 145, 'description':
    'Upper middle-right room'}, 'Meeting Room': {'x': 800, 'y': 145,
    'description': 'Upper-right room near angled wall'}, 'Right Corridor':
    {'x': 1070, 'y': 350, 'description': 'Right-side vertical corridor'},
    'Right Room': {'x': 1080, 'y': 520, 'description': 'Lower-right room'},
    'North Stairs': {'x': 460, 'y': 85, 'description': 'Top middle stairs'},
    'South Stairs': {'x': 560, 'y': 720, 'description':
    'Bottom middle stairs'}}}}
ROOM_SLOT_OFFSETS = [{'x': 0, 'y': 0}, {'x': 35, 'y': 0}, {'x': -35, 'y': 0
    }, {'x': 0, 'y': 35}, {'x': 0, 'y': -35}, {'x': 35, 'y': 35}, {'x': -35,
    'y': 35}, {'x': 35, 'y': -35}, {'x': -35, 'y': -35}]


def infer_icon_type(node_type: str) ->str:
    return {'environment': 'env', 'safety': 'alarm', 'energy': 'energy',
        'occupancy': 'occupancy'}.get(node_type.lower(), 'default')


def get_room_coords_from_db(conn: sqlite3.Connection, building: str, floor:
    str, room: str):
    row = get_room_from_database(conn, building, floor, room)
    if not row:
        return None, None
    return row['x'], row['y']


def get_room_node_count(conn: sqlite3.Connection, building: str, floor: str,
    room: str, exclude_chip_mac: (str | None)=None):
    if exclude_chip_mac:
        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM devices
            WHERE building = ?
            AND floor = ?
            AND room = ?
            AND chip_mac != ?
            """
            , (building, floor, room, exclude_chip_mac))
    else:
        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM devices
            WHERE building = ?
            AND floor = ?
            AND room = ?
            """
            , (building, floor, room))
    return cur.fetchone()[0]


def get_auto_room_position(conn: sqlite3.Connection, building: str, floor:
    str, room: str, exclude_chip_mac: (str | None)=None):
    base_x, base_y = get_room_coords_from_db(conn, building, floor, room)
    if base_x is None or base_y is None:
        return None, None
    count = get_room_node_count(conn=conn, building=building, floor=floor,
        room=room, exclude_chip_mac=exclude_chip_mac)
    offset = ROOM_SLOT_OFFSETS[count % len(ROOM_SLOT_OFFSETS)]
    return base_x + offset['x'], base_y + offset['y']


def validate_provision_location_and_type(conn: sqlite3.Connection, device:
    Device):
    from app.services.provisioning import validate_provision_location_and_type
    return validate_provision_location_and_type(conn, device)


def db():
    from app.db.connection import get_db_connection
    return get_db_connection()



def hash_password(password: str, salt_hex: str, iterations: int=None):
    if iterations is None:
        iterations = PASSWORD_PBKDF2_ITERATIONS
    salt_bytes = bytes.fromhex(salt_hex)
    password_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
        salt_bytes, iterations)
    return password_hash.hex()


def create_password_hash(password: str):
    salt_hex = secrets.token_hex(16)
    password_hash = hash_password(password=password, salt_hex=salt_hex,
        iterations=PASSWORD_PBKDF2_ITERATIONS)
    return {'password_salt': salt_hex, 'password_hash': password_hash,
        'password_iterations': PASSWORD_PBKDF2_ITERATIONS}


def verify_password(password: str, user_row):
    if not user_row:
        return False
    user = dict(user_row)
    password_salt = user.get('password_salt')
    stored_hash = user.get('password_hash')
    iterations = user.get('password_iterations') or PASSWORD_PBKDF2_ITERATIONS
    if not password_salt or not stored_hash:
        return False
    calculated_hash = hash_password(password=password, salt_hex=
        password_salt, iterations=iterations)
    return hmac.compare_digest(calculated_hash, stored_hash)


def safe_user_dict(user_row):
    if not user_row:
        return None
    user = dict(user_row)
    user.pop('password_salt', None)
    user.pop('password_hash', None)
    user.pop('password_iterations', None)
    return user


def ensure_default_admin_user():
    conn = db()
    try:
        existing_admin = conn.execute(
            """
        SELECT *
        FROM users
        WHERE email = ?
        LIMIT 1
    """
            , (ADMIN_EMAIL,)).fetchone()
        if not existing_admin:
            password_data = create_password_hash(ADMIN_PASSWORD)
            conn.execute(
                """
            INSERT INTO users(
                name,
                email,
                role,
                enabled,
                password_salt,
                password_hash,
                password_iterations,
                password_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
                , ('System Admin', ADMIN_EMAIL, 'admin', 1, password_data[
                'password_salt'], password_data['password_hash'], password_data
                ['password_iterations']))
            conn.commit()
        else:
            admin = dict(existing_admin)
            needs_update = False
            if admin.get('role') != 'admin':
                needs_update = True
            if admin.get('enabled') != 1:
                needs_update = True
            if not admin.get('password_hash'):
                needs_update = True
            if needs_update:
                password_data = create_password_hash(ADMIN_PASSWORD)
                conn.execute(
                    """
                UPDATE users
                SET role = 'admin',
                    enabled = 1,
                    password_salt = ?,
                    password_hash = ?,
                    password_iterations = ?,
                    password_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """
                    , (password_data['password_salt'], password_data[
                    'password_hash'], password_data['password_iterations'],
                    admin['id']))
                conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def create_session_token():
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    return raw_token, token_hash


def cleanup_expired_sessions(conn):
    conn.execute("""
        UPDATE auth_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE revoked_at IS NULL
          AND datetime(expires_at) <= datetime('now')
    """)
    conn.commit()


def create_login_session(conn, user_id: int, request: Request = None):
    cleanup_expired_sessions(conn)
    raw_token, token_hash = create_session_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    ip_address = None
    user_agent = None
    if request:
        if getattr(request, "client", None):
            ip_address = request.client.host
        user_agent = request.headers.get('user-agent')
    conn.execute("""
        INSERT INTO auth_sessions(user_id, token_hash, expires_at, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, token_hash, expires_at, ip_address, user_agent))
    conn.commit()
    return raw_token


def get_current_user_from_request(request: Request):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    conn = db()
    try:
        session = conn.execute(
            """
        SELECT *
        FROM auth_sessions
        WHERE token_hash = ?
          AND revoked_at IS NULL
          AND datetime(expires_at) > datetime('now')
        LIMIT 1
    """
            , (token_hash,)).fetchone()
        if not session:
            return None
        user = conn.execute(
            """
        SELECT *
        FROM users
        WHERE id = ?
          AND enabled = 1
        LIMIT 1
    """
            , (session['user_id'],)).fetchone()
        if not user:
            return None
        return safe_user_dict(user)
    finally:
        conn.close()


def revoke_current_session(request: Request):
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    conn = db()
    try:
        conn.execute(
            """
        UPDATE auth_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE token_hash = ?
          AND revoked_at IS NULL
    """
            , (token_hash,))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def default_redirect_for_user(user):
    role = (user.get('role') or 'client').lower()
    if role == 'admin':
        return '/admin'
    return f"/client-portal?user_id={user['id']}"


def render_role_login_page(request: Request, login_type: str, title: str,
    subtitle: str, badge: str, accent: str):
    return templates.TemplateResponse(request, 'role_login.html', {
        'request': request, 'login_type': login_type, 'title': title,
        'subtitle': subtitle, 'badge': badge, 'accent': accent})


@app.middleware('http')
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    public_paths = ('/', '/login', '/admin-login', '/client-login',
        '/auth/login', '/auth/status', '/logout', '/me', '/docs', '/redoc',
        '/openapi.json', '/favicon.ico', '/service-worker.js', '/manifest.json',
        '/healthz')
    if path in public_paths:
        return await call_next(request)
    public_prefixes = ('/uploads', '/ttn-webhook', '/static')
    for prefix in public_prefixes:
        if path == prefix or path.startswith((prefix + '/', prefix + '?')):
            return await call_next(request)
    if path == '/client-portal' or path.startswith('/client-portal/'):
        current_user = get_current_user_from_request(request)
        if not current_user:
            return login_redirect_for_request(request, '/client-login')
        role = (current_user.get('role') or '').lower()
        if role == 'admin':
            return await call_next(request)
        if role != 'client':
            return JSONResponse({'detail': 'Client role required'},
                status_code=403)
        requested_user_id = get_requested_client_user_id(request)
        if requested_user_id is None:
            if path == '/client-portal':
                return RedirectResponse(url=
                    f"/client-portal?user_id={current_user['id']}", status_code=302
                    )
            return JSONResponse({'detail': 'User ID required'}, status_code=400)
        if requested_user_id != current_user['id']:
            return JSONResponse({'detail':
                'You can only access your own client portal'}, status_code=403)
        return await call_next(request)

    # Deny-by-default: require authenticated admin for all other routes
    current_user = get_current_user_from_request(request)
    if not current_user:
        return login_redirect_for_request(request, '/admin-login')
    role = (current_user.get('role') or '').lower()
    if role != 'admin':
        return JSONResponse({'detail': 'Admin role required'},
            status_code=403)
    return await call_next(request)


@app.middleware('http')
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Permissions-Policy',
        'camera=(), microphone=(), geolocation=()',
    )
    if request.url.path.startswith('/auth/') or request.url.path in {
        '/me',
        '/logout',
        '/provision',
    }:
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


def is_admin_path(path: str):
    """
    Return True when a path must be accessible only by an
    authenticated administrator.
    """
    admin_prefixes = ('/admin', '/audit-log', '/alarms', '/alarm-settings',
        '/alarm-settings-page', '/users', '/user-access',
        '/client-access-manager',
        '/gateway-monitor', '/gateway-placement',
        '/gateway-placement-editor', '/floor-editor', '/floor-live-view',
        '/building-overview', '/device-capabilities-manager',
        '/sensor-profile-manager', '/sensor-profile-editor',
        '/sensor-profiles', '/sensor-catalog-manager', '/sensor-catalog',
        '/firmware-module-manager', '/firmware-modules',
        # Asset hierarchy, inventory and telemetry. These were reachable
        # anonymously: the whole building structure could be read, rewritten
        # or deleted without a session. TTN's webhook remains public at the
        # transport layer and authenticates with its shared secret. Provisioning
        # returns device root keys, so it is admin-only.
        '/clients', '/sites', '/buildings', '/floors', '/rooms',
        '/hierarchy', '/floorplans', '/floor-map-data',
        '/devices', '/devices-status', '/device-status',
        '/gateways',
        '/floorplan-editor', '/node-placement-editor', '/site-map-editor',
        '/api/analytics', '/api/devices', '/api/firmware')
    for prefix in admin_prefixes:
        if path == prefix or path.startswith(prefix + '/'):
            return True
    return False


def login_redirect_for_request(request: Request, login_path: str='/admin-login'
    ):
    next_path = request.url.path
    if request.url.query:
        next_path += '?' + request.url.query
    return RedirectResponse(url=login_path + '?next=' + quote(next_path),
        status_code=302)


def get_requested_client_user_id(request: Request):
    path = request.url.path
    query_user_id = request.query_params.get('user_id')
    if query_user_id:
        try:
            return int(query_user_id)
        except Exception:
            return None
    parts = path.strip('/').split('/')
    if len(parts) >= 2 and parts[0] == 'client-portal':
        try:
            return int(parts[1])
        except Exception:
            return None
    return None


def normalize_mac(mac: str) ->str:
    cleaned = re.sub('[^0-9A-Fa-f]', '', mac).lower()
    if len(cleaned) != 12:
        raise ValueError('chip_mac must contain exactly 12 hex characters')
    return cleaned


def normalize_eui(eui: str, expected_len: int) ->str:
    cleaned = re.sub('[^0-9A-Fa-f]', '', eui).upper()
    if len(cleaned) != expected_len:
        raise ValueError(
            f'EUI/key must contain exactly {expected_len} hex characters')
    return cleaned


def make_device_id(chip_mac: str) ->str:
    return f'node-{chip_mac}'


def generate_dev_eui() ->str:
    return secrets.token_hex(8).upper()


def generate_app_key() ->str:
    return secrets.token_hex(16).upper()


def validate_config() ->None:
    if not TTN_BASE or not APP_ID or not API_KEY:
        raise RuntimeError(
            'TTN_BASE_URL, TTN_APP_ID, and TTN_API_KEY must be set in .env')
    normalize_eui(JOIN_EUI, 16)
    normalize_eui(LORAWAN_APP_KEY, 32)


def safe_json_or_text(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return resp.text


def normalize_tb_value(value):
    if value in ['true', 'True', True]:
        return True
    if value in ['false', 'False', False]:
        return False
    try:
        return float(value)
    except Exception:
        return value


def get_existing_device(conn: sqlite3.Connection, chip_mac: str):
    """
    Return the complete device record.

    SELECT * is intentional here because the device table now
    contains profile, configuration and hierarchy columns in
    addition to the original legacy columns.
    """
    return conn.execute(
        """
        SELECT *
        FROM devices
        WHERE chip_mac = ?
        LIMIT 1
        """
        , (chip_mac,)).fetchone()


def get_device_by_device_id(conn: sqlite3.Connection, device_id: str):
    """
    Return the complete device record by its public device ID,
    including joined hierarchy fields (building, floor, room)
    that were previously flat columns.
    """
    return conn.execute(
        """
        SELECT 
            d.*,
            r.room_name as room,
            f.name as floor,
            b.name as building
        FROM devices d
        LEFT JOIN rooms r ON d.room_id = r.id
        LEFT JOIN floors f ON f.id = COALESCE(d.floor_id, r.floor_id)
        LEFT JOIN buildings b ON f.building_id = b.id
        WHERE d.device_id = ?
        LIMIT 1
        """
        , (device_id,)).fetchone()


def get_room_from_database(conn: sqlite3.Connection, building: str, floor: str, room_name: str):
    cur = conn.execute(
        """
        SELECT r.id, r.floor_id, b.name AS building, f.name AS floor, r.room_name, r.x, r.y
        FROM rooms r
        LEFT JOIN floors f ON r.floor_id = f.id
        LEFT JOIN buildings b ON f.building_id = b.id
        WHERE b.name = ?
        AND f.name = ?
        AND r.room_name = ?
        LIMIT 1
    """,
        (building, floor, room_name),
    )
    return cur.fetchone()


def get_room_by_id_for_provision(conn: sqlite3.Connection, room_id: int):
    from app.services.provisioning import get_room_by_id_for_provision
    return get_room_by_id_for_provision(conn, room_id)


def get_device_metadata_by_device_id(conn: sqlite3.Connection, device_id: str):
    return get_device_by_device_id(conn, device_id)


def dev_eui_exists(conn: sqlite3.Connection, dev_eui: str) ->bool:
    cur = conn.execute('SELECT 1 FROM devices WHERE dev_eui = ? LIMIT 1', (
        dev_eui,))
    return cur.fetchone() is not None


def generate_unique_dev_eui(conn: sqlite3.Connection) ->str:
    for _ in range(20):
        candidate = generate_dev_eui()
        if not dev_eui_exists(conn, candidate):
            return candidate
    raise RuntimeError('Failed to generate unique DevEUI')


def create_ttn_device_identity(device_id: str, dev_eui: str, join_eui: str
    ) ->None:
    ttn_client.create_application_device(device_id, dev_eui, join_eui, name
        =device_id, description='Provisioned by Raspberry Pi backend')


def set_ttn_join_server(device_id: str, dev_eui: str, join_eui: str,
    app_key: str) ->None:
    ttn_client.create_join_server_entry(device_id, dev_eui, join_eui, app_key)


def set_ttn_network_server(device_id: str, dev_eui: str, join_eui: str) ->None:
    ttn_client.create_network_server_entry(device_id, dev_eui, join_eui)


def set_ttn_application_server(device_id: str, dev_eui: str, join_eui: str
    ) ->None:
    ttn_client.create_application_server_entry(device_id, dev_eui, join_eui)


def set_device_formatter(device_id: str, node_type: str) ->None:
    formatter_code = FORMATTERS.get(node_type)
    if not formatter_code:
        return
    url = f'{TTN_BASE}/api/v3/as/applications/{APP_ID}/devices/{device_id}'
    payload = {'end_device': {'ids': {'device_id': device_id,
        'application_ids': {'application_id': APP_ID}}, 'formatters': {
        'up_formatter': 'FORMATTER_JAVASCRIPT', 'up_formatter_parameter':
        formatter_code}}, 'field_mask': {'paths': ['ids.device_id',
        'ids.application_ids.application_id', 'formatters.up_formatter',
        'formatters.up_formatter_parameter']}}
    resp = requests.put(url, headers=HEADERS, json=payload, timeout=20)
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail={'stage':
            'formatter_set', 'status_code': resp.status_code, 'response':
            safe_json_or_text(resp)})


# Cayenne LPP is decoded by the network server's own built-in formatter, which
# takes no JavaScript at all. A profile using it therefore stores an empty
# ttn_formatter_code, which is legitimate rather than a misconfiguration.
TTN_FORMATTER_TYPE_MAP = {'javascript': 'FORMATTER_JAVASCRIPT',
    'formatter_javascript': 'FORMATTER_JAVASCRIPT',
    'cayenne': 'FORMATTER_CAYENNELPP',
    'cayenne_lpp': 'FORMATTER_CAYENNELPP',
    'cayennelpp': 'FORMATTER_CAYENNELPP',
    'formatter_cayennelpp': 'FORMATTER_CAYENNELPP'}

# Formatter types the network server implements itself; these must be sent
# without an up_formatter_parameter.
TTN_BUILTIN_FORMATTER_TYPES = {'FORMATTER_CAYENNELPP'}


def normalize_ttn_formatter_type(formatter_type: (str | None)):
    """
    Convert the profile-facing formatter type into the exact TTN
    API enum.
    """
    clean_type = str(formatter_type or '').strip().lower()
    ttn_type = TTN_FORMATTER_TYPE_MAP.get(clean_type)
    if not ttn_type:
        raise HTTPException(status_code=400, detail={'message':
            'Unsupported TTN formatter type', 'submitted': formatter_type,
            'supported': sorted(TTN_FORMATTER_TYPE_MAP.keys())})
    return ttn_type


def build_profile_formatter_update_payload(device_id: str, formatter_type:
    str, formatter_code: str):
    """
    Build the TTN Application Server formatter-update request using
    the code stored in the selected sensor profile.
    """
    clean_device_id = str(device_id or '').strip()
    clean_formatter_code = str(formatter_code or '').strip()
    if not clean_device_id:
        raise HTTPException(status_code=400, detail='device_id is required')
    normalized_formatter_type = normalize_ttn_formatter_type(formatter_type)
    builtin = normalized_formatter_type in TTN_BUILTIN_FORMATTER_TYPES
    if not builtin:
        if not clean_formatter_code:
            raise HTTPException(status_code=400, detail=
                'Profile TTN formatter code is empty')
        if 'decodeUplink' not in clean_formatter_code:
            raise HTTPException(status_code=400, detail=
                'Profile TTN formatter must contain decodeUplink')
    # A built-in formatter is selected by name; sending a parameter alongside
    # it is rejected by the Application Server, so the field is cleared.
    return {'end_device': {'ids': {'device_id': clean_device_id,
        'application_ids': {'application_id': APP_ID}}, 'formatters': {
        'up_formatter': normalized_formatter_type, 'up_formatter_parameter':
        '' if builtin else clean_formatter_code}}, 'field_mask': {'paths': [
        'ids.device_id', 'ids.application_ids.application_id',
        'formatters.up_formatter', 'formatters.up_formatter_parameter']}}


def set_device_formatter_from_profile(device_id: str, profile: dict):
    """
    Install the TTN uplink formatter stored in a validated sensor
    profile.

    This function does not use FORMATTERS or node_type.
    """
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail=
            'A validated sensor-profile dictionary is required')
    profile_code = str(profile.get('profile_code') or '').strip().upper()
    profile_version = int(profile.get('profile_version') or 0)
    formatter_type = str(profile.get('ttn_formatter_type') or '').strip()
    formatter_code = str(profile.get('ttn_formatter_code') or '').strip()
    encoder_key = str(profile.get('payload_encoder_key') or '').strip().lower()
    # A Cayenne LPP profile ships no JavaScript because the network server
    # decodes the format natively. Without this, registering such a device
    # fails on an empty formatter and leaves it half-created in TTN.
    if not formatter_code and (
        encoder_key.startswith('cayenne')
        or formatter_type.lower() in ('', 'none', 'cayenne', 'cayenne_lpp')
    ):
        formatter_type = 'cayenne_lpp'
    if not profile_code:
        raise HTTPException(status_code=400, detail='Profile code is missing')
    if profile_version <= 0:
        raise HTTPException(status_code=400, detail=
            'Profile version is invalid')
    payload = build_profile_formatter_update_payload(device_id=device_id,
        formatter_type=formatter_type, formatter_code=formatter_code)
    url = f'{TTN_BASE}/api/v3/as/applications/{APP_ID}/devices/{device_id}'
    response = requests.put(url, headers=HEADERS, json=payload, timeout=20)
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail={'stage':
            'profile_formatter_set', 'profile_code': profile_code,
            'profile_version': profile_version, 'status_code': response.
            status_code, 'response': safe_json_or_text(response)})
    return {'status': 'formatter_synced', 'device_id': device_id,
        'profile_code': profile_code, 'profile_version': profile_version,
        'ttn_formatter_type': payload['end_device']['formatters'][
        'up_formatter'], 'formatter_code_length': len(formatter_code)}


def register_device_in_ttn(device_id: str, dev_eui: str, join_eui: str,
    app_key: str, profile: dict):
    """
    Register a new end device in every required TTN component and
    install the uplink formatter stored in its sensor profile.

    Registration spans four components plus a formatter update, and TTN offers
    no transaction across them. A failure partway used to leave a half-created
    device that could neither join nor be registered again: the retry got
    409 id_taken from the identity that had already been written. Anything
    created here is therefore removed again if a later stage fails, so a retry
    starts from a clean slate.
    """
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail=
            'A validated sensor profile is required for TTN registration')

    try:
        create_ttn_device_identity(device_id, dev_eui, join_eui)
    except requests.HTTPError as error:
        response = getattr(error, 'response', None)
        if response is not None and response.status_code == 409:
            raise HTTPException(status_code=409, detail={'stage':
                'device_identity', 'message':
                'Device is already registered in TTN',
                'device_id': device_id, 'dev_eui': dev_eui}) from error
        raise

    try:
        set_ttn_join_server(device_id, dev_eui, join_eui, app_key)
        set_ttn_network_server(device_id, dev_eui, join_eui)
        set_ttn_application_server(device_id, dev_eui, join_eui)
        formatter_result = set_device_formatter_from_profile(device_id=
            device_id, profile=profile)
    except Exception:
        try:
            ttn_client.delete_device(device_id)
            logger.info(
                'Rolled back partial TTN registration for %s', device_id)
        except Exception as cleanup_error:
            # Surfaced rather than swallowed: the device is now half-created
            # and a human has to remove it before registration can succeed.
            logger.error(
                'Could not roll back partial TTN registration for %s: %s. '
                'Delete it in the TTN console before retrying.',
                device_id, cleanup_error)
        raise

    return {'status': 'registered', 'device_id': device_id, 'profile_code':
        profile['profile_code'], 'profile_version': profile[
        'profile_version'], 'formatter': formatter_result}


def save_device(conn: sqlite3.Connection, chip_mac: str, device_id: str,
    dev_eui: str, join_eui: str, app_key: str, node_type: str, building:
    str, floor: str, room: str, label: str, x: (int | None), y: (int | None
    ), icon_type: str) ->None:
    conn.execute(
        """
        INSERT INTO devices (
            chip_mac, device_id, dev_eui, join_eui, app_key,
            node_type, building, floor, room, label, x, y, icon_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
        , (chip_mac, device_id, dev_eui, join_eui, app_key, node_type,
        building, floor, room, label, x, y, icon_type))
    conn.commit()


def update_existing_device_metadata(conn: sqlite3.Connection, chip_mac: str,
    node_type: str, building: str, floor: str, room: str, label: str, x: (
    int | None), y: (int | None), icon_type: str) ->None:
    conn.execute(
        """
        UPDATE devices
        SET node_type = ?,
            building = ?,
            floor = ?,
            room = ?,
            label = ?,
            x = ?,
            y = ?,
            icon_type = ?
        WHERE chip_mac = ?
    """
        , (node_type, building, floor, room, label, x, y, icon_type, chip_mac))
    conn.commit()


def save_device_profile_assignment(conn: sqlite3.Connection, chip_mac: str,
    profile: dict):
    """
    Store the backend-validated sensor profile against a device.

    The values in profile must come from
    resolve_sensor_profile_for_provision(), not directly from the
    browser or ESP32 request.
    """
    conn.execute(
        """
        UPDATE devices
        SET
            profile_id = ?,
            profile_code = ?,
            profile_version = ?,
            payload_version = ?,
            firmware_version = ?,
            configuration_status = ?,
            configuration_error = NULL,
            configuration_updated_at = CURRENT_TIMESTAMP,
            configuration_checksum = ?
        WHERE chip_mac = ?
        """
        , (profile['profile_id'], profile['profile_code'], profile[
        'profile_version'], profile['payload_version'], profile[
        'firmware_version'], profile['configuration_status'], profile[
        'configuration_checksum'], chip_mac))


def build_profile_provision_response(status: str, device_row, profile: dict,
    capabilities: list[str]):
    """
    Build the response returned to the LILYGO after provisioning.
    """
    return {'status': status, 'device_id': device_row['device_id'],
        'dev_eui': device_row['dev_eui'], 'join_eui': device_row['join_eui'
        ], 'app_key': device_row['app_key'], 'profile_id': profile[
        'profile_id'], 'profile_code': profile['profile_code'],
        'profile_name': profile['profile_name'], 'profile_version': profile
        ['profile_version'], 'node_type': profile['node_type'],
        'capabilities': capabilities, 'payload_version': profile[
        'payload_version'], 'payload_encoder_key': profile[
        'payload_encoder_key'], 'f_port': profile['f_port'],
        'uplink_interval_seconds': profile['uplink_interval_seconds'],
        'firmware_version': profile['firmware_version'],
        'configuration_status': profile['configuration_status'],
        'configuration_checksum': profile['configuration_checksum'],
        'building': device_row['building'], 'floor': device_row['floor'],
        'room': device_row['room'], 'room_id': device_row['room_id'],
        'label': device_row['label'], 'x': device_row['x'], 'y': device_row
        ['y'], 'icon_type': device_row['icon_type']}


def tb_login(force_refresh=False):
    return tb_client.login(force_refresh=force_refresh)


def tb_headers(force_refresh=False):
    return tb_client.get_headers(force_refresh=force_refresh)


def tb_request(method, path, **kwargs):
    return tb_client.request(method, path, **kwargs)


def tb_profile_name_for_node_type(node_type: str) ->str:
    return {'environment': 'environment_profile', 'safety':
        'safety_profile', 'occupancy': 'occupancy_profile', 'energy':
        'energy_profile'}.get(node_type, 'default')


def tb_get_device_by_name(device_name: str):
    return tb_client.get_device_by_name(device_name)


def tb_get_or_create_device(device_name: str, node_type: str):
    existing = tb_get_device_by_name(device_name)
    if existing:
        return existing
    payload = {'name': device_name, 'type': node_type, 'deviceProfileName':
        tb_profile_name_for_node_type(node_type)}
    r = tb_request('POST', '/api/device', json=payload)
    return r.json()


def validate_tb_sensor_profile_mapping(profile: dict):
    """
    Validate the ThingsBoard configuration stored in a sensor
    profile and return its canonical mapping.
    """
    if not isinstance(profile, dict):
        raise HTTPException(status_code=400, detail=
            'A complete sensor-profile dictionary is required')
    profile_code = str(profile.get('profile_code') or '').strip().upper()
    profile_name = str(profile.get('profile_name') or '').strip()
    node_type = str(profile.get('node_type') or '').strip().lower()
    tb_device_profile_name = str(profile.get('tb_device_profile_name') or ''
        ).strip()
    profile_version = int(profile.get('profile_version') or 0)
    if not profile_code:
        raise HTTPException(status_code=400, detail=
            'Sensor profile code is missing')
    if not profile_name:
        raise HTTPException(status_code=400, detail=
            'Sensor profile name is missing')
    if not node_type:
        raise HTTPException(status_code=400, detail=
            'Sensor profile node type is missing')
    if profile_version <= 0:
        raise HTTPException(status_code=400, detail=
            'Sensor profile version is invalid')
    if not tb_device_profile_name:
        raise HTTPException(status_code=409, detail={'message':
            'The sensor profile has no ThingsBoard device-profile mapping',
            'profile_code': profile_code, 'action':
            'Set tb_device_profile_name in the Sensor Profile Manager'})
    if not bool(profile.get('enabled')):
        raise HTTPException(status_code=409, detail=
            'The sensor profile is disabled')
    if str(profile.get('status') or '').strip().lower() != 'active':
        raise HTTPException(status_code=409, detail=
            'The sensor profile is not active')
    return {'profile_code': profile_code, 'profile_name': profile_name,
        'profile_version': profile_version, 'node_type': node_type,
        'tb_device_profile_name': tb_device_profile_name}


def build_tb_device_payload_from_profile(device_name: str, profile: dict):
    """
    Build the ThingsBoard device-creation payload using the
    mapping stored in the selected sensor profile.
    """
    clean_device_name = str(device_name or '').strip()
    if not clean_device_name:
        raise HTTPException(status_code=400, detail=
            'ThingsBoard device name is required')
    mapping = validate_tb_sensor_profile_mapping(profile)
    return {'name': clean_device_name, 'type': mapping['node_type'],
        'deviceProfileName': mapping['tb_device_profile_name'], 'label':
        mapping['profile_name']}


def ensure_tb_device_from_profile(device_name: str, profile: dict):
    """
    Get or create a ThingsBoard device using the sensor profile's
    ThingsBoard device-profile mapping.
    """
    payload = build_tb_device_payload_from_profile(device_name=device_name,
        profile=profile)
    existing = tb_get_device_by_name(device_name)
    if existing:
        return {'status': 'existing', 'created': False, 'device': existing,
            'device_name': device_name, 'profile_code': profile[
            'profile_code'], 'profile_version': profile['profile_version'],
            'tb_device_profile_name': payload['deviceProfileName'],
            'requested_payload': payload}
    response = tb_request('POST', '/api/device', json=payload)
    created_device = response.json()
    return {'status': 'created', 'created': True, 'device': created_device,
        'device_name': device_name, 'profile_code': profile['profile_code'],
        'profile_version': profile['profile_version'],
        'tb_device_profile_name': payload['deviceProfileName'],
        'requested_payload': payload}


def build_tb_profile_attributes(device_row, profile: dict):
    """
    Build profile-driven server attributes for a ThingsBoard
    device.
    """
    device = dict(device_row) if not isinstance(device_row, dict
        ) else device_row
    mapping = validate_tb_sensor_profile_mapping(profile)
    telemetry_fields = []
    for field in profile.get('fields', []):
        telemetry_fields.append({'field_key': field.get('field_key'),
            'label': field.get('label'), 'unit': field.get('unit'),
            'data_type': field.get('data_type'), 'display_order': field.get
            ('display_order'), 'visible_floor': bool(field.get(
            'visible_floor')), 'visible_dashboard': bool(field.get(
            'visible_dashboard'))})
    enabled_rule_codes = [rule.get('rule_code') for rule in profile.get(
        'rules', []) if bool(rule.get('enabled'))]
    return {'building': device.get('building'), 'floor': device.get('floor'
        ), 'room': device.get('room'), 'room_id': device.get('room_id'),
        'client_id': device.get('client_id'), 'site_id': device.get(
        'site_id'), 'building_id': device.get('building_id'), 'floor_id':
        device.get('floor_id'), 'label': device.get('label'), 'x': device.
        get('x'), 'y': device.get('y'), 'node_type': mapping['node_type'],
        'profile_id': profile.get('id'), 'profile_code': mapping[
        'profile_code'], 'profile_name': mapping['profile_name'],
        'profile_version': mapping['profile_version'], 'payload_version':
        profile.get('payload_version'), 'payload_encoder_key': profile.get(
        'payload_encoder_key'), 'tb_device_profile_name': mapping[
        'tb_device_profile_name'], 'capabilities':
        profile_unique_string_list(profile.get('capabilities', [])),
        'icon_type': profile.get('icon_type'), 'icon_color': profile.get(
        'icon_color'), 'telemetry_fields': telemetry_fields,
        'alarm_rule_codes': enabled_rule_codes, 'configuration_checksum':
        device.get('configuration_checksum'), 'configuration_status':
        device.get('configuration_status')}


def sync_tb_attributes_from_profile(device_row, profile: dict, tb_device: (
    dict | None)=None):
    """
    Create or find the ThingsBoard device and synchronize its
    profile-driven server attributes.
    """
    device = dict(device_row) if not isinstance(device_row, dict
        ) else device_row
    device_name = str(device.get('device_id') or '').strip()
    if not device_name:
        raise HTTPException(status_code=400, detail=
            'Device row has no device_id')
    ensure_result = None
    if tb_device is None:
        ensure_result = ensure_tb_device_from_profile(device_name=
            device_name, profile=profile)
        tb_device = ensure_result['device']
    attributes = build_tb_profile_attributes(device_row=device, profile=profile
        )
    tb_send_attributes(tb_device, attributes)
    return {'status': 'attributes_synced', 'device_id': device_name,
        'profile_code': profile['profile_code'], 'profile_version': profile
        ['profile_version'], 'tb_device_profile_name': profile[
        'tb_device_profile_name'], 'device': tb_device, 'ensure_result':
        ensure_result, 'attribute_count': len(attributes), 'attributes':
        attributes}


def tb_send_telemetry(device: dict, data: dict):
    device_id = device['id']['id']
    tb_request('POST',
        f'/api/plugins/telemetry/DEVICE/{device_id}/timeseries/ANY', json=data)


def tb_send_attributes(device: dict, attributes: dict):
    device_id = device['id']['id']
    tb_request('POST',
        f'/api/plugins/telemetry/DEVICE/{device_id}/SERVER_SCOPE', json=
        attributes)


def sync_tb_attributes_from_row(row):
    tb_device = tb_get_or_create_device(row['device_id'], row['node_type'])
    tb_send_attributes(tb_device, {'building': row['building'], 'floor':
        row['floor'], 'room': row['room'], 'label': row['label'],
        'node_type': row['node_type'], 'x': row['x'], 'y': row['y'],
        'icon_type': row['icon_type']})
    return tb_device


def check_thresholds(node_type: str, data: dict, capabilities: (list[str] |
    None)=None):
    from app.services.alarm_engine import check_thresholds as svc_check_thresholds
    return svc_check_thresholds(node_type, data, capabilities)


def enrich_battery_telemetry(telemetry: dict):
    from app.services.alarm_engine import (
        enrich_battery_telemetry as svc_enrich_battery_telemetry,
    )
    return svc_enrich_battery_telemetry(telemetry)


def check_battery_alarms(telemetry: dict):
    from app.services.alarm_engine import (
        check_battery_alarms as svc_check_battery_alarms,
    )
    return svc_check_battery_alarms(telemetry)


def enrich_signal_telemetry(ttn_data: dict, telemetry: dict):
    from app.services.alarm_engine import (
        enrich_signal_telemetry as svc_enrich_signal_telemetry,
    )
    return svc_enrich_signal_telemetry(ttn_data, telemetry)


def check_signal_alarms(telemetry: dict):
    from app.services.alarm_engine import check_signal_alarms as svc_check_signal_alarms
    return svc_check_signal_alarms(telemetry)


def save_alarm_history(conn: sqlite3.Connection, device_id: str, node_type:
    str, building: str, floor: str, room: str, alarm_message: str,
    telemetry: dict, alarm_type: str = 'system', commit: bool = True):
    conn.execute(
        """
        INSERT INTO alarm_history (
            device_id,
            node_type,
            building,
            floor,
            room,
            alarm_type,
            alarm_message,
            telemetry
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
        , (device_id, node_type, building, floor, room, alarm_type,
        alarm_message, json.dumps(telemetry)))
    if commit:
        conn.commit()


def save_latest_telemetry(conn: sqlite3.Connection, device_id: str,
    telemetry: dict, commit: bool = True):
    alarm_active = 1 if telemetry.get('alarm_active') in [True, 'true', 1, '1'
        ] else 0
    alarm_message = telemetry.get('alarm_message', 'OK')
    conn.execute(
        """
        INSERT INTO device_latest_telemetry(
            device_id,
            telemetry,
            alarm_active,
            alarm_message,
            updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(device_id)
        DO UPDATE SET
            telemetry = excluded.telemetry,
            alarm_active = excluded.alarm_active,
            alarm_message = excluded.alarm_message,
            updated_at = CURRENT_TIMESTAMP
    """
        , (device_id, json.dumps(telemetry), alarm_active, alarm_message))
    conn.execute(
        """
        INSERT INTO historical_telemetry(device_id, telemetry)
        VALUES (?, ?)
    """
        , (device_id, json.dumps(telemetry)))
    if commit:
        conn.commit()


def get_local_latest_telemetry(conn, device_id: str):
    row = conn.execute(
        """
        SELECT *
        FROM device_latest_telemetry
        WHERE device_id = ?
    """
        , (device_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        telemetry = json.loads(item['telemetry']) if item['telemetry'] else {}
    except Exception:
        telemetry = {}
    telemetry['alarm_active'] = bool(item['alarm_active'])
    telemetry['alarm_message'] = item['alarm_message']
    telemetry['local_updated_at'] = item['updated_at']
    return telemetry


def build_audit_message(action, actor='admin', target_type=None, target_id=
    None, details=None):
    details = details or {}
    if action == 'create_user':
        name = details.get('name', f'user {target_id}')
        email = details.get('email', '')
        return f'User {name} ({email}) was created.'
    if action == 'enable_user':
        name = details.get('name', f'user {target_id}')
        email = details.get('email', '')
        return f'User {name} ({email}) was enabled.'
    if action == 'disable_user':
        name = details.get('name', f'user {target_id}')
        email = details.get('email', '')
        return f'User {name} ({email}) was disabled.'
    if action == 'delete_user':
        deleted_user = details.get('deleted_user', {})
        name = deleted_user.get('name', f'user {target_id}')
        email = deleted_user.get('email', '')
        return f'User {name} ({email}) was deleted.'
    if action == 'update_user':
        new_user = details.get('new', {})
        name = new_user.get('name', f'user {target_id}')
        email = new_user.get('email', '')
        return f'User {name} ({email}) was updated.'
    if action == 'create_user_access':
        user = details.get('user', {})
        name = user.get('name', 'unknown user')
        email = user.get('email', '')
        return f'Access permissions were granted to {name} ({email}).'
    if action == 'update_user_access':
        old = details.get('old', {})
        new = details.get('new', {})
        permission_labels = {'can_view_devices': 'devices',
            'can_view_gateways': 'gateways', 'can_view_alarms': 'alarms',
            'can_view_telemetry': 'telemetry', 'can_manage_email_settings':
            'email settings'}
        changes = []
        for key, label in permission_labels.items():
            if old.get(key) != new.get(key):
                old_value = 'ON' if old.get(key) else 'OFF'
                new_value = 'ON' if new.get(key) else 'OFF'
                changes.append(f'{label}: {old_value} → {new_value}')
        if changes:
            return 'Client access permissions were updated: ' + ', '.join(
                changes) + '.'
        return 'Client access permissions were updated.'
    if action == 'delete_user_access':
        deleted_access = details.get('deleted_access', {})
        user_id = deleted_access.get('user_id', 'unknown user')
        return f'Access permissions were removed for user ID {user_id}.'
    if action == 'acknowledge_alarm':
        new_alarm = details.get('new', {})
        alarm_message = new_alarm.get('alarm_message', 'Alarm')
        device_id = new_alarm.get('device_id', 'unknown device')
        acknowledged_by = new_alarm.get('acknowledged_by', actor)
        return (
            f'{alarm_message} alarm for {device_id} was acknowledged by {acknowledged_by}.'
            )
    if action == 'resolve_alarm':
        new_alarm = details.get('new', {})
        alarm_message = new_alarm.get('alarm_message', 'Alarm')
        device_id = new_alarm.get('device_id', 'unknown device')
        resolved_by = new_alarm.get('resolved_by', actor)
        return (
            f'{alarm_message} alarm for {device_id} was resolved by {resolved_by}.'
            )
    if action == 'create_gateway':
        gateway = details.get('gateway', {})
        name = gateway.get('label') or gateway.get('name') or gateway.get(
            'gateway_id') or f'gateway {target_id}'
        return f'Gateway {name} was created.'
    if action == 'move_gateway':
        old = details.get('old', {})
        new = details.get('new', {})
        name = new.get('label') or new.get('name') or new.get('gateway_id'
            ) or f'gateway {target_id}'
        old_x = old.get('x')
        old_y = old.get('y')
        new_x = new.get('x')
        new_y = new.get('y')
        return (
            f'Gateway {name} was moved from ({old_x}, {old_y}) to ({new_x}, {new_y}).'
            )
    if action == 'update_gateway':
        new = details.get('new', {})
        changed_fields = details.get('changed_fields', {})
        name = new.get('label') or new.get('name') or new.get('gateway_id'
            ) or f'gateway {target_id}'
        if changed_fields:
            fields = ', '.join(changed_fields.keys())
            return f'Gateway {name} was updated. Changed fields: {fields}.'
        return f'Gateway {name} was updated.'
    readable_action = action.replace('_', ' ')
    if target_type and target_id:
        return f'{readable_action} on {target_type} {target_id}.'
    return readable_action + '.'


def log_audit_event(conn, action, actor='admin', target_type=None,
    target_id=None, details=None, message=None, client_id=None, site_id=
    None, building_id=None, floor_id=None, room_id=None, device_id=None,
    gateway_id=None, user_id=None):
    try:
        details = details or {}
        if message is None:
            message = build_audit_message(action=action, actor=actor,
                target_type=target_type, target_id=target_id, details=details)
        stored_details = redact_secrets(details)
        conn.execute(
            """
            INSERT INTO audit_log(
                actor,
                action,
                target_type,
                target_id,
                message,
                details,
                client_id,
                site_id,
                building_id,
                floor_id,
                room_id,
                device_id,
                gateway_id,
                user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
            , (actor, action, target_type, str(target_id) if target_id is not
            None else None, message, json.dumps(stored_details, default=str),
            client_id, site_id, building_id, floor_id, room_id, device_id,
            gateway_id, user_id))
        conn.commit()
    except Exception as e:
        logger.error("Audit log error: %s", e, exc_info=True)



def get_device_scope_for_audit(conn, device_id):
    scope = {'client_id': None, 'site_id': None, 'building_id': None,
        'floor_id': None, 'room_id': None, 'device_id': device_id}
    if not device_id:
        return scope
    try:
        row = conn.execute(
            """
            SELECT
                d.device_id AS device_id,
                d.room_id AS room_id,
                f.id AS floor_id,
                f.building_id AS building_id,
                b.site_id AS site_id,
                s.client_id AS client_id
            FROM devices d
            LEFT JOIN rooms r ON d.room_id = r.id
            LEFT JOIN floors f ON f.id = COALESCE(d.floor_id, r.floor_id)
            LEFT JOIN buildings b ON f.building_id = b.id
            LEFT JOIN sites s ON b.site_id = s.id
            WHERE d.device_id = ?
            LIMIT 1
        """
            , (device_id,)).fetchone()
        if row:
            scope['client_id'] = row['client_id']
            scope['site_id'] = row['site_id']
            scope['building_id'] = row['building_id']
            scope['floor_id'] = row['floor_id']
            scope['room_id'] = row['room_id']
            scope['device_id'] = row['device_id']
    except Exception as e:
        logger.error('Device audit scope lookup error: %s', e)
    return scope


LIVE_TELEMETRY_SYSTEM_KEYS = ('alarm_active', 'alarm_message',
    'battery_percent', 'battery_voltage', 'battery_status', 'power_source',
    'signal_status', 'gateway_id', 'rssi', 'snr', 'spreading_factor',
    'bandwidth', 'frequency', 'received_by_gateways', 'profile_code',
    'profile_version', 'payload_version', 'firmware_version', 'node_type',
    'profile_alarm_active_count', 'profile_alarm_rule_codes')


def load_device_profile_for_live_telemetry(conn: sqlite3.Connection,
    device_id: str):
    """
    Load the sensor profile assigned to a device for portal and
    ThingsBoard telemetry rendering.
    """
    clean_device_id = str(device_id or '').strip()
    if not clean_device_id:
        return None
    device_row = conn.execute(
        """
        SELECT
            profile_id,
            profile_code
        FROM devices
        WHERE device_id = ?
        LIMIT 1
        """
        , (clean_device_id,)).fetchone()
    if not device_row:
        return None
    device = dict(device_row)
    profile_identifier = device.get('profile_id') or device.get('profile_code')
    if not profile_identifier:
        return None
    try:
        return get_sensor_profile_detail(conn, profile_identifier,
            include_formatter=False)
    except Exception as exc:
        logger.error('Live telemetry profile lookup failed: %s', exc)
        return None


def build_profile_live_field_metadata(profile: (dict | None), surface: str=
    'all'):
    """
    Return ordered telemetry-field metadata for portal rendering.

    surface:
        all       -> every profile field
        floor     -> fields enabled for Floor Live View
        dashboard -> fields enabled for dashboards
    """
    if not isinstance(profile, dict):
        return []
    clean_surface = str(surface or 'all').strip().lower()
    result = []
    for field in profile.get('fields', []):
        field_key = str(field.get('field_key') or '').strip()
        if not field_key:
            continue
        if clean_surface == 'floor' and not bool(field.get('visible_floor')):
            continue
        if clean_surface == 'dashboard' and not bool(field.get(
            'visible_dashboard')):
            continue
        result.append({'field_key': field_key, 'label': field.get('label') or
            field_key.replace('_', ' ').title(), 'unit': field.get('unit'),
            'data_type': field.get('data_type') or 'number',
            'display_order': int(field.get('display_order') or 0),
            'precision_digits': field.get('precision_digits'), 'min_value':
            field.get('min_value'), 'max_value': field.get('max_value'),
            'visible_floor': bool(field.get('visible_floor')),
            'visible_dashboard': bool(field.get('visible_dashboard'))})
    result.sort(key=lambda field: (field['display_order'], field['field_key']))
    return result


def build_profile_live_metadata(profile: (dict | None)):
    """
    Build the profile metadata returned to the device and floor
    portals.
    """
    if not isinstance(profile, dict):
        return None
    return {'profile_id': profile.get('id'), 'profile_code': profile.get(
        'profile_code'), 'profile_name': profile.get('profile_name'),
        'profile_version': profile.get('profile_version'),
        'payload_version': profile.get('payload_version'), 'node_type':
        profile.get('node_type'), 'capabilities':
        profile_unique_string_list(profile.get('capabilities', [])),
        'icon_type': profile.get('icon_type'), 'icon_color': profile.get(
        'icon_color'), 'tb_device_profile_name': profile.get(
        'tb_device_profile_name')}


def get_profile_telemetry_keys(profile: (dict | None)):
    """
    Combine dynamic sensor fields with system telemetry fields.
    """
    keys = []
    for field in build_profile_live_field_metadata(profile, surface='all'):
        field_key = field['field_key']
        if field_key not in keys:
            keys.append(field_key)
    for system_key in LIVE_TELEMETRY_SYSTEM_KEYS:
        if system_key not in keys:
            keys.append(system_key)
    return keys


def read_tb_latest_telemetry(device_id: str, profile: (dict | None)=None):
    """
    Read ThingsBoard telemetry using keys declared by the assigned
    sensor profile instead of a fixed sensor-type list.

    Passing profile is optional to preserve compatibility with
    existing callers.
    """
    if settings.PREVIEW_MODE:
        conn = db()
        try:
            return get_local_latest_telemetry(conn, device_id) or {}
        finally:
            conn.close()
    clean_device_id = str(device_id or '').strip()
    telemetry = {'alarm_active': False, 'alarm_message': 'OK',
        'last_seen_ts': None, 'last_seen_iso': None,
        'last_seen_seconds_ago': None, 'device_status': 'offline'}
    if profile is None and clean_device_id:
        profile_conn = None
        try:
            profile_conn = db()
            profile = load_device_profile_for_live_telemetry(profile_conn,
                clean_device_id)
        except Exception as exc:
            logger.error('ThingsBoard profile lookup failed: %s', exc)
            profile = None
        finally:
            if profile_conn is not None:
                profile_conn.close()
    telemetry_fields = build_profile_live_field_metadata(profile, surface='all'
        )
    for field in telemetry_fields:
        telemetry.setdefault(field['field_key'], None)
    requested_keys = get_profile_telemetry_keys(profile)
    if not clean_device_id:
        return telemetry
    tb_device = tb_get_device_by_name(clean_device_id)
    if not tb_device:
        return telemetry
    try:
        tb_id = tb_device['id']['id']
    except Exception:
        logger.info('ThingsBoard device has no valid ID: %s', clean_device_id)
        return telemetry
    try:
        response = tb_request('GET',
            f'/api/plugins/telemetry/DEVICE/{tb_id}/values/timeseries',
            params={'keys': ','.join(requested_keys)})
    except Exception as exc:
        logger.error('ThingsBoard telemetry read failed: %s', exc)
        return telemetry
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    latest_ts = None
    for key in requested_keys:
        values = data.get(key)
        if not isinstance(values, list) or not values:
            continue
        latest_item = values[0]
        if not isinstance(latest_item, dict):
            continue
        telemetry[key] = normalize_tb_value(latest_item.get('value'))
        timestamp = latest_item.get('ts')
        if timestamp is not None:
            try:
                timestamp = int(timestamp)
                if latest_ts is None or timestamp > latest_ts:
                    latest_ts = timestamp
            except Exception:
                pass
    if latest_ts is not None:
        import time
        now_ms = int(time.time() * 1000)
        seconds_ago = max(0, int((now_ms - latest_ts) / 1000))
        telemetry['last_seen_ts'] = latest_ts
        telemetry['last_seen_iso'] = datetime.fromtimestamp(latest_ts / 
            1000, tz=timezone.utc).isoformat()
        telemetry['last_seen_seconds_ago'] = seconds_ago
        telemetry['device_status'
            ] = 'online' if seconds_ago <= 300 else 'offline'
    return telemetry


def ttn_get_gateway_status(gateway_id: str):
    return ttn_client.get_gateway_info(gateway_id)


def ttn_get_gateway_connection_stats(gateway_id: str):
    return ttn_client.get_gateway_stats(gateway_id)


def sensor_profile_actor_from_request(request: Request) ->str:
    """
    Resolve the administrator responsible for a profile change.
    """
    try:
        user = get_current_user_from_request(request)
    except Exception:
        user = None
    if not user:
        return 'admin_api'
    return str(user.get('email') or user.get('username') or user.get('name'
        ) or user.get('id') or 'admin_api')


def normalize_sensor_catalog_definition(conn: sqlite3.Connection, data:
    dict, exclude_sensor_id: (int | None)=None):
    """
    Validate and normalize one physical-sensor catalog record.

    Sensor catalog records describe hardware supported by firmware.
    They do not install Arduino libraries or alter firmware binaries.
    """
    if not isinstance(data, dict):
        raise ValueError('Sensor definition must be a JSON object')
    sensor_code = str(data.get('sensor_code') or '').strip().upper()
    manufacturer = str(data.get('manufacturer') or '').strip()
    model = str(data.get('model') or '').strip()
    use_case = str(data.get('use_case') or '').strip().lower()
    protocol = str(data.get('protocol') or '').strip().lower()
    default_bus = str(data.get('default_bus') or '').strip()
    default_address = str(data.get('default_address') or '').strip()
    datasheet_url = str(data.get('datasheet_url') or '').strip()
    description = str(data.get('description') or '').strip()
    enabled = profile_value_to_boolean(data.get('enabled', True), default=True)
    errors = []
    if not PROFILE_CODE_PATTERN.fullmatch(sensor_code):
        errors.append(
            'sensor_code must start with an uppercase letter and contain only A-Z, 0-9 and underscores'
            )
    if not model:
        errors.append('model is required')
    if len(model) > 120:
        errors.append('model must not exceed 120 characters')
    if manufacturer and len(manufacturer) > 120:
        errors.append('manufacturer must not exceed 120 characters')
    if not PROFILE_KEY_PATTERN.fullmatch(use_case):
        errors.append(
            'use_case must start with a lowercase letter and contain only lowercase letters, numbers and underscores'
            )
    if not PROFILE_KEY_PATTERN.fullmatch(protocol):
        errors.append(
            'protocol must start with a lowercase letter and contain only lowercase letters, numbers and underscores'
            )
    if datasheet_url and not datasheet_url.lower().startswith(('http://',
        'https://')):
        errors.append('datasheet_url must start with http:// or https://')
    duplicate_sql = """
        SELECT id
        FROM sensor_catalog
        WHERE UPPER(sensor_code) = UPPER(?)
    """
    duplicate_parameters = [sensor_code]
    if exclude_sensor_id is not None:
        duplicate_sql += ' AND id != ?'
        duplicate_parameters.append(exclude_sensor_id)
    duplicate_sql += ' LIMIT 1'
    if sensor_code and conn.execute(duplicate_sql, duplicate_parameters
        ).fetchone():
        errors.append(f'sensor_code already exists: {sensor_code}')
    if errors:
        raise ValueError('; '.join(errors))
    return {'sensor_code': sensor_code, 'manufacturer': manufacturer or
        None, 'model': model, 'use_case': use_case, 'protocol': protocol,
        'default_bus': default_bus or None, 'default_address': 
        default_address or None, 'datasheet_url': datasheet_url or None,
        'description': description or None, 'enabled': enabled}


def get_sensor_catalog_detail(conn: sqlite3.Connection, sensor_id: int):
    """Return one sensor catalog record with profile usage count."""
    row = conn.execute(
        """
        SELECT
            catalog.*,
            COUNT(DISTINCT relation.profile_id) AS profile_count
        FROM sensor_catalog catalog
        LEFT JOIN sensor_profile_sensors relation
            ON relation.sensor_id = catalog.id
        WHERE catalog.id = ?
        GROUP BY catalog.id
        LIMIT 1
        """
        , (sensor_id,)).fetchone()
    if not row:
        return None
    sensor = dict(row)
    sensor['enabled'] = bool(sensor.get('enabled'))
    sensor['can_delete'] = int(sensor.get('profile_count') or 0) == 0
    return sensor


def set_sensor_catalog_enabled_api(sensor_id: int, request: Request,
    enabled: bool):
    """Enable or disable one sensor catalog record."""
    conn = db()
    try:
        sensor = get_sensor_catalog_detail(conn, sensor_id)
        if not sensor:
            raise HTTPException(status_code=404, detail=
                'Sensor catalog record not found')
        actor = sensor_profile_actor_from_request(request)
        conn.execute(
            """
            UPDATE sensor_catalog
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """
            , (int(enabled), profile_utc_now_iso(), sensor_id))
        conn.commit()
        updated_sensor = get_sensor_catalog_detail(conn, sensor_id)
        action = ('enable_sensor_catalog' if enabled else
            'disable_sensor_catalog')
        log_audit_event(conn, action=action, actor=actor, target_type=
            'sensor_catalog', target_id=sensor_id, details={'sensor':
            updated_sensor}, message=('Enabled' if enabled else 'Disabled') +
            ' sensor catalog record: ' + str(updated_sensor.get('sensor_code'))
            )
        return {'status': 'enabled' if enabled else 'disabled', 'message': 
            'Sensor catalog record enabled' if enabled else
            'Sensor catalog record disabled', 'sensor': updated_sensor}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as error:
        conn.rollback()
        logger.error('Sensor catalog status error: %s', error)
        raise HTTPException(status_code=500, detail=
            'Could not change sensor catalog status')
    finally:
        conn.close()


def normalize_firmware_module_definition(conn: sqlite3.Connection, data:
    dict, exclude_module_id: (int | None)=None):
    """
    Validate and normalize a firmware-module catalog record.

    A firmware module describes a driver that is already compiled into
    a compatible LILYGO firmware image. Creating this record does not
    remotely install an Arduino library or modify device firmware.
    """
    if not isinstance(data, dict):
        raise ValueError('Firmware module definition must be a JSON object')
    module_key = str(data.get('module_key') or '').strip().lower()
    display_name = str(data.get('display_name') or '').strip()
    driver_class = str(data.get('driver_class') or '').strip()
    protocol = str(data.get('protocol') or '').strip().lower()
    library_name = str(data.get('library_name') or '').strip()
    library_version = str(data.get('library_version') or '').strip()
    supported_board = str(data.get('supported_board') or 'LILYGO LoRa32'
        ).strip()
    min_firmware_version = str(data.get('min_firmware_version') or '').strip()
    source_file = str(data.get('source_file') or '').strip()
    notes = str(data.get('notes') or '').strip()
    enabled = profile_value_to_boolean(data.get('enabled', True), default=True)
    errors = []
    if not PROFILE_KEY_PATTERN.fullmatch(module_key):
        errors.append(
            'module_key must start with a lowercase letter and contain only lowercase letters, numbers and underscores'
            )
    if not display_name:
        errors.append('display_name is required')
    if len(display_name) > 160:
        errors.append('display_name must not exceed 160 characters')
    if not driver_class:
        errors.append('driver_class is required')
    if len(driver_class) > 160:
        errors.append('driver_class must not exceed 160 characters')
    if not PROFILE_KEY_PATTERN.fullmatch(protocol):
        errors.append(
            'protocol must start with a lowercase letter and contain only lowercase letters, numbers and underscores'
            )
    if not supported_board:
        errors.append('supported_board is required')
    if min_firmware_version and firmware_version_tuple(min_firmware_version
        ) is None:
        errors.append(
            'min_firmware_version must use a numeric version such as 1.0.0')
    duplicate_sql = """
        SELECT id
        FROM firmware_modules
        WHERE LOWER(module_key) = LOWER(?)
    """
    duplicate_parameters = [module_key]
    if exclude_module_id is not None:
        duplicate_sql += ' AND id != ?'
        duplicate_parameters.append(exclude_module_id)
    duplicate_sql += ' LIMIT 1'
    if module_key and conn.execute(duplicate_sql, duplicate_parameters
        ).fetchone():
        errors.append(f'module_key already exists: {module_key}')
    if errors:
        raise ValueError('; '.join(errors))
    return {'module_key': module_key, 'display_name': display_name,
        'driver_class': driver_class, 'protocol': protocol, 'library_name':
        library_name or None, 'library_version': library_version or None,
        'supported_board': supported_board, 'min_firmware_version': 
        min_firmware_version or None, 'source_file': source_file or None,
        'notes': notes or None, 'enabled': enabled}


def get_firmware_module_detail(conn: sqlite3.Connection, module_id: int):
    """Return one firmware module with profile-usage information."""
    row = conn.execute(
        """
        SELECT
            module.*,

            (
                SELECT COUNT(DISTINCT relation.profile_id)
                FROM sensor_profile_sensors relation
                WHERE relation.firmware_module_id = module.id
            ) AS sensor_profile_count,

            (
                SELECT COUNT(DISTINCT compatibility.profile_id)
                FROM profile_firmware_compatibility compatibility
                WHERE compatibility.firmware_module_id = module.id
            ) AS compatibility_profile_count

        FROM firmware_modules module
        WHERE module.id = ?
        LIMIT 1
        """
        , (module_id,)).fetchone()
    if not row:
        return None
    module = dict(row)
    module['enabled'] = bool(module.get('enabled'))
    profile_ids = conn.execute(
        """
        SELECT DISTINCT profile_id
        FROM (
            SELECT profile_id
            FROM sensor_profile_sensors
            WHERE firmware_module_id = ?

            UNION

            SELECT profile_id
            FROM profile_firmware_compatibility
            WHERE firmware_module_id = ?
        )
        """
        , (module_id, module_id)).fetchall()
    module['profile_count'] = len(profile_ids)
    module['can_delete'] = module['profile_count'] == 0
    return module


def set_firmware_module_enabled_api(module_id: int, request: Request,
    enabled: bool):
    """Enable or disable a firmware module without deleting history."""
    conn = db()
    try:
        module = get_firmware_module_detail(conn, module_id)
        if not module:
            raise HTTPException(status_code=404, detail=
                'Firmware module not found')
        actor = sensor_profile_actor_from_request(request)
        conn.execute(
            """
            UPDATE firmware_modules
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """
            , (int(enabled), profile_utc_now_iso(), module_id))
        conn.commit()
        updated_module = get_firmware_module_detail(conn, module_id)
        action = ('enable_firmware_module' if enabled else
            'disable_firmware_module')
        log_audit_event(conn, action=action, actor=actor, target_type=
            'firmware_module', target_id=module_id, details={'module':
            updated_module}, message=('Enabled' if enabled else 'Disabled') +
            ' firmware module: ' + str(updated_module.get('module_key')))
        return {'status': 'enabled' if enabled else 'disabled', 'message': 
            'Firmware module enabled' if enabled else
            'Firmware module disabled', 'module': updated_module}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as error:
        conn.rollback()
        logger.error('Firmware module status error: %s', error)
        raise HTTPException(status_code=500, detail=
            'Could not change firmware module status')
    finally:
        conn.close()


def record_device_configuration_history(conn: sqlite3.Connection,
    device_row, profile: dict, provision_status: str, source: str=
    'lilygo_provisioning', requested_by: (str | None)=None):
    """
    Record one successfully applied device configuration.

    device_row must be the complete database device row.
    profile must contain backend-validated canonical values.
    """
    if not device_row:
        raise ValueError(
            'Cannot record configuration history without a device row')
    device_id = str(device_row['device_id'] or '').strip()
    if not device_id:
        raise ValueError(
            'Cannot record configuration history without device_id')
    configuration_payload = {'event': provision_status, 'device': {
        'device_id': device_id, 'chip_mac': device_row['chip_mac'],
        'node_type': profile['node_type'], 'label': device_row['label']},
        'profile': {'profile_id': profile['profile_id'], 'profile_code':
        profile['profile_code'], 'profile_name': profile['profile_name'],
        'profile_version': profile['profile_version'], 'capabilities':
        profile['capabilities']}, 'payload': {'payload_version': profile[
        'payload_version'], 'payload_encoder_key': profile[
        'payload_encoder_key'], 'f_port': profile['f_port'],
        'uplink_interval_seconds': profile['uplink_interval_seconds']},
        'firmware': {'firmware_version': profile['firmware_version']},
        'location': {'client_id': device_row['client_id'], 'site_id':
        device_row['site_id'], 'building_id': device_row['building_id'],
        'floor_id': device_row['floor_id'], 'room_id': device_row['room_id'
        ], 'building': device_row['building'], 'floor': device_row['floor'],
        'room': device_row['room'], 'x': device_row['x'], 'y': device_row[
        'y']}, 'display': {'icon_type': device_row['icon_type']},
        'configuration_status': profile['configuration_status'],
        'configuration_checksum': profile['configuration_checksum']}
    conn.execute(
        """
        INSERT INTO device_configuration_history(
            device_id,
            profile_id,
            profile_code,
            profile_version,
            payload_version,
            firmware_version,
            configuration_status,
            configuration_payload_json,
            configuration_checksum,
            error_message,
            source,
            requested_by,
            applied_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            CURRENT_TIMESTAMP
        )
        """
        , (device_id, profile['profile_id'], profile['profile_code'],
        profile['profile_version'], profile['payload_version'], profile[
        'firmware_version'], profile['configuration_status'], json.dumps(
        configuration_payload, ensure_ascii=False, sort_keys=True), profile
        ['configuration_checksum'], None, source, requested_by or
        device_row['chip_mac']))


def merge_profile_live_telemetry_sources(tb_telemetry: (dict | None),
    local_telemetry: (dict | None)):
    """
    Merge ThingsBoard and locally persisted telemetry.

    Locally persisted telemetry represents the latest backend-
    validated payload, so it takes priority when both sources
    contain the same key.
    """
    tb_data = dict(tb_telemetry) if isinstance(tb_telemetry, dict) else {}
    local_data = dict(local_telemetry) if isinstance(local_telemetry, dict
        ) else {}
    telemetry = dict(tb_data)
    if local_data:
        telemetry.update(local_data)
    tb_metadata_keys = {'alarm_active', 'alarm_message', 'last_seen_ts',
        'last_seen_iso', 'last_seen_seconds_ago', 'device_status'}
    tb_has_data = tb_data.get('last_seen_ts') is not None
    if not tb_has_data:
        tb_has_data = any(value is not None for key, value in tb_data.items
            () if key not in tb_metadata_keys)
    local_has_data = bool(local_data)
    local_updated_at = local_data.get('local_updated_at')
    if local_updated_at:
        updated_datetime = parse_datetime_safe(local_updated_at)
        if updated_datetime:
            now_datetime = datetime.now(timezone.utc)
            seconds_ago = max(0, int((now_datetime - updated_datetime).
                total_seconds()))
            telemetry['last_seen_iso'] = updated_datetime.isoformat()
            telemetry['last_seen_seconds_ago'] = seconds_ago
            telemetry['device_status'
                ] = 'online' if seconds_ago <= 300 else 'offline'
    telemetry.setdefault('alarm_active', False)
    telemetry.setdefault('alarm_message', 'OK')
    telemetry.setdefault('last_seen_ts', None)
    telemetry.setdefault('last_seen_iso', None)
    telemetry.setdefault('last_seen_seconds_ago', None)
    telemetry.setdefault('device_status', 'offline')
    if tb_has_data and local_has_data:
        source = 'thingsboard+local_latest'
    elif local_has_data:
        source = 'local_latest'
    elif tb_has_data:
        source = 'thingsboard'
    else:
        source = 'none'
    telemetry['telemetry_source'] = source
    return telemetry


def build_profile_display_telemetry(profile: (dict | None), telemetry: (
    dict | None), surface: str='dashboard'):
    """
    Combine profile field metadata with current telemetry values
    so portals can render readings without hardcoded sensor names.
    """
    telemetry_data = telemetry if isinstance(telemetry, dict) else {}
    display_fields = []
    field_metadata = build_profile_live_field_metadata(profile, surface=surface
        )
    for field in field_metadata:
        item = dict(field)
        field_key = item['field_key']
        value = telemetry_data.get(field_key)
        precision_digits = item.get('precision_digits')
        if value is not None and precision_digits is not None and isinstance(
            value, (int, float)) and not isinstance(value, bool):
            value = round(value, int(precision_digits))
        item['value'] = value
        item['has_value'] = value is not None
        display_fields.append(item)
    return display_fields


def send_alarm_email(device_id: str, node_type: str, room: str, alarms:
    list[str], telemetry: dict):
    if not ALERT_EMAIL_ENABLED:
        logger.info('Email alert disabled')
        return
    if not ALERT_EMAIL_FROM or not ALERT_EMAIL_PASSWORD:
        logger.info('Email sender config missing')
        return
    conn = db()
    try:
        recipients = conn.execute(
            """
        SELECT email
        FROM alarm_recipients
        WHERE enabled = 1
    """
            ).fetchall()
        template = conn.execute(
            """
        SELECT subject_template, body_template
        FROM alarm_email_template
        WHERE id = 1
    """
            ).fetchone()
        if not recipients:
            logger.info('No enabled alarm recipients')
            return
        if not template:
            logger.info('No email template found')
            return
        recipient_list = [r['email'] for r in recipients]
        alarms_text = '\n'.join('- ' + a for a in alarms)
        telemetry_text = json.dumps(telemetry, indent=2)
        template_data = {'device_id': device_id, 'node_type': node_type, 'room':
            room, 'alarms': alarms_text, 'telemetry': telemetry_text}
        try:
            subject = template['subject_template'].format(**template_data)
            body = template['body_template'].format(**template_data)
        except Exception as e:
            logger.error('Template formatting error: %s', e)
            return
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = ALERT_EMAIL_FROM
        msg['To'] = ', '.join(recipient_list)
        try:
            # Without an explicit timeout a hung SMTP host blocks this call
            # indefinitely, which stalls the alarm path that calls it.
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
                server.starttls()
                server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
                server.sendmail(ALERT_EMAIL_FROM, recipient_list, msg.as_string())
            logger.info('Alarm email sent successfully')
        except Exception as e:
            logger.error('Failed to send alarm email: %s', e)
    finally:
        conn.close()


def get_user_access_rows(conn, user_id: int):
    return conn.execute(
        """
        SELECT *
        FROM user_access
        WHERE user_id = ?
    """
        , (user_id,)).fetchall()


def user_can_access_floor(conn, user_id: int, floor_id: int):
    floor = conn.execute(
        """
        SELECT *
        FROM floors
        WHERE id = ?
    """,
        (floor_id,)).fetchone()
    if not floor:
        return None
    building = conn.execute(
        """
        SELECT *
        FROM buildings
        WHERE id = ?
    """
        , (floor['building_id'],)).fetchone()
    site = None
    client_id = None
    site_id = None
    building_id = floor['building_id']
    if building:
        site_id = building['site_id']
        site = conn.execute(
            """
            SELECT *
            FROM sites
            WHERE id = ?
        """
            , (site_id,)).fetchone()
        if site:
            client_id = site['client_id']
    access_rows = get_user_access_rows(conn, user_id)
    for access in access_rows:
        if access['floor_id'] and access['floor_id'] == floor_id:
            return access
        if access['building_id'] and access['building_id'
            ] == building_id and not access['floor_id']:
            return access
        if access['site_id'] and access['site_id'] == site_id and not access[
            'building_id'] and not access['floor_id']:
            return access
        if access['client_id'] and access['client_id'
            ] == client_id and not access['site_id'] and not access[
            'building_id'] and not access['floor_id']:
            return access
    return None


def get_ids(conn, query, params=()):
    rows = conn.execute(query, params).fetchall()
    return [row['id'] for row in rows]


def get_room_ids_for_floors(conn, floor_ids):
    if not floor_ids:
        return []
    placeholders = ','.join('?' for _ in floor_ids)
    rows = conn.execute(
        f"""
        SELECT id
        FROM rooms
        WHERE floor_id IN ({placeholders})
    """
        , floor_ids).fetchall()
    return [row['id'] for row in rows]


def get_floorplan_ids_for_floors_and_rooms(conn, floor_ids, room_ids):
    floorplan_ids = set()
    if floor_ids:
        placeholders = ','.join('?' for _ in floor_ids)
        rows = conn.execute(
            f"""
            SELECT id
            FROM floorplans
            WHERE floor_id IN ({placeholders})
        """
            , floor_ids).fetchall()
        for row in rows:
            floorplan_ids.add(row['id'])
    # Rooms are linked to floors, not directly to floorplans, so the floor
    # lookup above already covers every floorplan reachable from these rooms.
    return list(floorplan_ids)


def delete_by_ids(conn, table, ids):
    if not ids:
        return 0
    placeholders = ','.join('?' for _ in ids)
    cur = conn.execute(
        f"""
        DELETE FROM {table}
        WHERE id IN ({placeholders})
    """
        , ids)
    return cur.rowcount


def table_has_column(conn, table_name, column_name):
    columns = conn.execute(f'PRAGMA table_info({table_name})').fetchall()
    return any(col[1] == column_name for col in columns)


def safe_unassign_gateways(conn, client_ids=None, site_ids=None,
    building_ids=None, floor_ids=None, clear_client=False, clear_site=False,
    clear_building=False, clear_floor=False):
    client_ids = client_ids or []
    site_ids = site_ids or []
    building_ids = building_ids or []
    floor_ids = floor_ids or []
    where_parts = []
    params = []

    def add_where(column_name, values):
        if values and table_has_column(conn, 'gateways', column_name):
            placeholders = ','.join('?' for _ in values)
            where_parts.append(f'{column_name} IN ({placeholders})')
            params.extend(values)
    add_where('client_id', client_ids)
    add_where('site_id', site_ids)
    add_where('building_id', building_ids)
    add_where('floor_id', floor_ids)
    if not where_parts:
        return 0
    set_parts = []
    if clear_client and table_has_column(conn, 'gateways', 'client_id'):
        set_parts.append('client_id = NULL')
    if clear_site and table_has_column(conn, 'gateways', 'site_id'):
        set_parts.append('site_id = NULL')
    if clear_building and table_has_column(conn, 'gateways', 'building_id'):
        set_parts.append('building_id = NULL')
    if clear_floor and table_has_column(conn, 'gateways', 'floor_id'):
        set_parts.append('floor_id = NULL')
    if table_has_column(conn, 'gateways', 'x'):
        set_parts.append('x = NULL')
    if table_has_column(conn, 'gateways', 'y'):
        set_parts.append('y = NULL')
    if not set_parts:
        return 0
    where_sql = ' OR '.join(where_parts)
    count = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM gateways
        WHERE {where_sql}
    """
        , params).fetchone()[0]
    conn.execute(
        f"""
        UPDATE gateways
        SET {', '.join(set_parts)}
        WHERE {where_sql}
    """
        , params)
    return count


def parse_datetime_safe(value):
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            dt = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def get_device_capabilities_list(conn, device_id: str, node_type: (str |
    None)=None):
    rows = conn.execute(
        """
        SELECT capability
        FROM device_capabilities
        WHERE device_id = ?
        ORDER BY capability
    """
        , (device_id,)).fetchall()
    capabilities = [r['capability'] for r in rows]
    if capabilities:
        return capabilities
    if node_type in ['environment', 'energy', 'safety', 'occupancy']:
        return [node_type]
    if node_type == 'multi':
        return ['environment', 'energy', 'safety', 'occupancy']
    return []


def normalize_provision_capabilities(node_type: str, capabilities: (list |
    None)=None):
    from app.services.provisioning import normalize_provision_capabilities
    return normalize_provision_capabilities(node_type, capabilities)


def save_device_capabilities(conn, device_id: str, capabilities: list[str]):
    conn.execute(
        """
        DELETE FROM device_capabilities
        WHERE device_id = ?
    """
        , (device_id,))
    for cap in capabilities:
        conn.execute(
            """
            INSERT OR IGNORE INTO device_capabilities(
                device_id,
                capability
            )
            VALUES (?, ?)
        """
            , (device_id, cap))


PROFILE_SYSTEM_TELEMETRY_FIELDS = {'battery_percent', 'battery_voltage',
    'power_source', 'firmware_version', 'payload_version'}


def apply_device_label_uplink(conn, device: dict, frm_payload) -> dict:
    """
    Store a nickname sent by a device over LoRa.

    The nickname is cosmetic: identity stays the DevEUI, and nothing about
    routing, placement or telemetry depends on it. It exists so a person
    looking at the discovery inbox can tell which physical box is which.
    """
    device_id = device.get('device_id')
    label = parse_label_uplink(frm_payload)
    if not label:
        logger.info(
            'Ignored unusable nickname uplink from %s on port %s',
            device_id, LABEL_F_PORT)
        return {'status': 'label_rejected', 'device_id': device_id,
            'telemetry_stored': False, 'thingsboard_sent': False}

    previous = str(device.get('label') or '')
    if previous == label:
        return {'status': 'label_unchanged', 'device_id': device_id,
            'label': label, 'telemetry_stored': False,
            'thingsboard_sent': False}

    conn.execute(
        'UPDATE devices SET label = ?, updated_at = CURRENT_TIMESTAMP'
        ' WHERE device_id = ?', (label, device_id))
    conn.commit()
    try:
        log_audit_event(conn, action='device_label_from_uplink',
            target_type='device', target_id=device_id, actor='lora',
            details={'label': label, 'previous': previous})
    except Exception as exc:
        logger.info('Nickname audit write failed for %s: %s', device_id, exc)

    logger.info('Device %s named "%s" over the air', device_id, label)
    return {'status': 'label_applied', 'device_id': device_id,
        'label': label, 'telemetry_stored': False, 'thingsboard_sent': False}


def learn_profile_fields(conn: sqlite3.Connection, profile: dict,
    telemetry: dict) -> list:
    """
    Declare fields this profile has not met before.

    A decoded uplink names its own values and they are stored either way,
    so this is about what a declared field adds around the value: a
    readable label instead of a raw key, display order, and a target for
    an alarm rule. Doing it on first sight means a new kind of device is
    usable without anyone authoring a profile by hand.

    Never fatal. A device reporting telemetry matters more than the
    profile catching up with it, so any failure is logged and the uplink
    carries on being processed.
    """
    if not settings.PROFILE_FIELD_LEARNING_ENABLED:
        return []
    profile_id = profile.get('id')
    if not profile_id or not telemetry:
        return []

    try:
        declared = {str(row['field_key']).strip().lower()
            for row in conn.execute(
                'SELECT field_key FROM sensor_profile_fields'
                ' WHERE profile_id = ?', (profile_id,))}
        already_learned = conn.execute(
            'SELECT COUNT(*) FROM sensor_profile_fields'
            ' WHERE profile_id = ? AND auto_learned = 1',
            (profile_id,)).fetchone()[0]

        planned = plan_learned_fields(telemetry, declared,
            already_learned=already_learned)
        if not planned:
            return []

        next_order = conn.execute(
            'SELECT COALESCE(MAX(display_order), 0)'
            ' FROM sensor_profile_fields WHERE profile_id = ?',
            (profile_id,)).fetchone()[0]

        added = []
        for offset, field in enumerate(planned, start=1):
            conn.execute(
                """
                INSERT INTO sensor_profile_fields (
                    profile_id, field_key, label, data_type,
                    payload_order, required, nullable, display_order,
                    visible_floor, visible_dashboard, auto_learned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """
                , (profile_id, field['field_key'], field['label'],
                field['data_type'], next_order + offset,
                field['required'], field['nullable'], next_order + offset,
                field['visible_floor'], field['visible_dashboard']))
            added.append(field['field_key'])
        conn.commit()

        log_audit_event(conn, action='profile_fields_learned',
            target_type='sensor_profile',
            target_id=str(profile.get('profile_code') or profile_id),
            actor='uplink', details={'fields': added})
        logger.info('Profile %s learned new fields: %s',
            profile.get('profile_code'), ', '.join(added))
        return added
    except Exception as exc:
        logger.info('Profile field learning skipped: %s', exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []

def auto_provision_discovered_device(conn: sqlite3.Connection, device_id: str, raw_envelope: dict | None = None) -> dict:
    """
    Zero-touch auto-provisioning for newly detected LoRaWAN devices.
    Assigns the universal Cayenne LPP profile (or default active profile),
    marks status as online and is_placed as 0 (unplaced).
    """
    clean_device_id = str(device_id or '').strip()
    dev_eui = None
    join_eui = None
    if raw_envelope and isinstance(raw_envelope, dict):
        end_device_ids = raw_envelope.get('end_device_ids') or {}
        dev_eui = str(end_device_ids.get('dev_eui') or '').strip() or None
        join_eui = str(end_device_ids.get('join_eui') or '').strip() or JOIN_EUI

    # Find the best profile: prefer CAYENNE_LPP_V1, then MULTI_LEGACY_V1, then any active
    profile_row = conn.execute(
        """SELECT * FROM sensor_profiles
           WHERE profile_code = 'CAYENNE_LPP_V1'
             AND enabled = 1
             AND LOWER(status) = 'active'"""
    ).fetchone()
    if not profile_row:
        profile_row = conn.execute(
            """SELECT * FROM sensor_profiles
               WHERE profile_code = 'MULTI_LEGACY_V1'
                 AND enabled = 1
                 AND LOWER(status) = 'active'"""
        ).fetchone()
    if not profile_row:
        profile_row = conn.execute(
            """SELECT * FROM sensor_profiles
               WHERE enabled = 1 AND LOWER(status) = 'active'
               ORDER BY id LIMIT 1"""
        ).fetchone()

    profile_id = profile_row['id'] if profile_row else None
    profile_code = profile_row['profile_code'] if profile_row else 'CAYENNE_LPP_V1'
    profile_version = int(profile_row['profile_version']) if profile_row else 1
    payload_version = int(profile_row['payload_version']) if profile_row else 1
    node_type = str(profile_row['node_type']) if profile_row else 'multi'
    schema_checksum = str(profile_row['schema_checksum'] or '') if profile_row else ''

    # Bound the discovery inbox. Each unknown DevEUI creates a devices row and
    # an audit row, so without a ceiling anyone able to reach the webhook could
    # grow the database indefinitely. Known devices never reach this path.
    unplaced_count = conn.execute(
        "SELECT COUNT(*) FROM devices WHERE is_placed = 0"
    ).fetchone()[0]
    if unplaced_count >= settings.MAX_UNPLACED_DISCOVERED_DEVICES:
        logger.warning(
            "Auto-discovery refused for %s: %s unplaced devices already awaiting "
            "commissioning (limit %s)",
            clean_device_id,
            unplaced_count,
            settings.MAX_UNPLACED_DISCOVERED_DEVICES,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "message": (
                    "Discovery inbox is full. Commission or remove unplaced "
                    "devices before new ones can be discovered."
                ),
                "unplaced_devices": unplaced_count,
                "limit": settings.MAX_UNPLACED_DISCOVERED_DEVICES,
            },
        )

    friendly_label = f"Discovered {clean_device_id}"
    chip_mac = dev_eui or clean_device_id

    conn.execute(
        """
        INSERT OR IGNORE INTO devices (
            chip_mac,
            device_id,
            dev_eui,
            join_eui,
            app_key,
            label,
            node_type,
            status,
            profile_id,
            profile_code,
            profile_version,
            payload_version,
            configuration_checksum,
            configuration_status,
            is_placed,
            last_seen,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, ?, ?, ?, 'valid', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            chip_mac,
            clean_device_id,
            dev_eui,
            join_eui,
            LORAWAN_APP_KEY or None,
            friendly_label,
            node_type,
            profile_id,
            profile_code,
            profile_version,
            payload_version,
            schema_checksum,
        ),
    )
    conn.commit()

    try:
        log_audit_event(
            conn,
            action="device_auto_discovered",
            target_type="device",
            target_id=clean_device_id,
            message=f"Zero-touch auto-discovered LoRaWAN device {clean_device_id} via TTN uplink",
        )
    except Exception as audit_err:
        logger.warning("Could not log audit event for auto-discovery: %s", audit_err)

    created_row = conn.execute(
        "SELECT * FROM devices WHERE device_id = ? LIMIT 1", (clean_device_id,)
    ).fetchone()
    if not created_row and dev_eui:
        created_row = conn.execute(
            "SELECT * FROM devices WHERE dev_eui = ? COLLATE NOCASE LIMIT 1",
            (dev_eui,),
        ).fetchone()
    if not created_row:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Could not associate the discovered TTN identity",
                "device_id": clean_device_id,
                "dev_eui": dev_eui,
            },
        )
    return dict(created_row) if created_row else {}


def load_device_profile_context_for_webhook(conn: sqlite3.Connection,
    device_id: str, raw_envelope: dict | None = None):
    """
    Load and verify the sensor profile assigned to the device
    that produced a TTN uplink. If unknown, auto-provisions the device.
    """
    clean_device_id = str(device_id or '').strip()
    if not clean_device_id:
        raise HTTPException(status_code=400, detail=
            'TTN webhook device_id is missing')
    end_device_ids = raw_envelope.get('end_device_ids') if raw_envelope else {}
    webhook_dev_eui = str((end_device_ids or {}).get('dev_eui') or '').strip() or None
    device_row = conn.execute(
        """
        SELECT *
        FROM devices
        WHERE device_id = ?
           OR (? IS NOT NULL AND dev_eui = ? COLLATE NOCASE)
        ORDER BY CASE WHEN device_id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """
        , (clean_device_id, webhook_dev_eui, webhook_dev_eui, clean_device_id)).fetchone()
    if not device_row:
        # A valid TTN uplink is the discovery event. Unknown devices are
        # inserted into the unplaced inbox and the same uplink continues
        # through validation and telemetry storage.
        device = auto_provision_discovered_device(
            conn,
            clean_device_id,
            raw_envelope,
        )
    else:
        device = dict(device_row)
        try:
            conn.execute("UPDATE devices SET status = 'online', last_seen = CURRENT_TIMESTAMP WHERE device_id = ?", (clean_device_id,))
            conn.commit()
        except Exception:
            pass

    profile_identifier = device.get('profile_id') or device.get('profile_code')
    if not profile_identifier:
        # Fallback to CAYENNE_LPP_V1 or default active profile
        fallback_profile = conn.execute(
            """SELECT id, profile_code FROM sensor_profiles
               WHERE enabled = 1 AND LOWER(status) = 'active'
               ORDER BY (CASE WHEN profile_code = 'CAYENNE_LPP_V1' THEN 0 ELSE 1 END), id
               LIMIT 1"""
        ).fetchone()
        if fallback_profile:
            conn.execute(
                "UPDATE devices SET profile_id = ?, profile_code = ? WHERE device_id = ?",
                (fallback_profile['id'], fallback_profile['profile_code'], clean_device_id)
            )
            conn.commit()
            profile_identifier = fallback_profile['id']
            device['profile_id'] = fallback_profile['id']
            device['profile_code'] = fallback_profile['profile_code']
        else:
            raise HTTPException(status_code=409, detail={'message':
                'Device has no assigned sensor profile', 'device_id':
                clean_device_id, 'action':
                'Reprovision the device using a sensor profile'})

    profile = get_sensor_profile_detail(conn, profile_identifier,
        include_formatter=False)
    if not profile:
        raise HTTPException(status_code=409, detail={'message':
            'The assigned sensor profile could not be found', 'device_id':
            clean_device_id, 'profile_id': device.get('profile_id'),
            'profile_code': device.get('profile_code')})
    device_profile_code = str(device.get('profile_code') or '').strip().upper()
    current_profile_code = str(profile.get('profile_code') or '').strip(
        ).upper()
    if device_profile_code != current_profile_code:
        raise HTTPException(status_code=409, detail={'message':
            'Device profile code does not match the database profile',
            'device_profile_code': device_profile_code,
            'database_profile_code': current_profile_code})
    device_profile_version = int(device.get('profile_version') or 0)
    current_profile_version = int(profile.get('profile_version') or 0)
    if device_profile_version != current_profile_version:
        raise HTTPException(status_code=409, detail={'message':
            'Device profile version is outdated', 'device_profile_version':
            device_profile_version, 'current_profile_version':
            current_profile_version, 'action':
            'Reprovision the device before accepting telemetry'})
    device_payload_version = int(device.get('payload_version') or 0)
    current_payload_version = int(profile.get('payload_version') or 0)
    if device_payload_version != current_payload_version:
        raise HTTPException(status_code=409, detail={'message':
            'Device payload version does not match its profile',
            'device_payload_version': device_payload_version,
            'profile_payload_version': current_payload_version})
    device_checksum = str(device.get('configuration_checksum') or '').strip()
    profile_checksum = str(profile.get('schema_checksum') or '').strip()
    if (device_checksum and profile_checksum and device_checksum !=
        profile_checksum):
        raise HTTPException(status_code=409, detail={'message':
            'Device configuration checksum does not match its profile',
            'action': 'Reprovision the device before accepting telemetry'})
    if not bool(profile.get('enabled')):
        raise HTTPException(status_code=409, detail=
            'The assigned sensor profile is disabled')
    if str(profile.get('status') or '').strip().lower() != 'active':
        raise HTTPException(status_code=409, detail=
            'The assigned sensor profile is not active')
    fields = profile.get('fields', [])
    fields_by_key = {str(field.get('field_key') or '').strip().lower():
        field for field in fields if str(field.get('field_key') or '').strip()}
    if not fields_by_key:
        raise HTTPException(status_code=409, detail={'message':
            'The assigned sensor profile has no telemetry fields',
            'profile_code': current_profile_code})
    return {'device': device, 'profile': profile, 'fields_by_key':
        fields_by_key}


def convert_profile_boolean_value(value):
    """
    Convert common TTN JSON Boolean representations.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        raise ValueError('Boolean number must be 0 or 1')
    clean_value = str(value or '').strip().lower()
    if clean_value in {'true', '1', 'yes', 'on', 'active', 'detected'}:
        return True
    if clean_value in {'false', '0', 'no', 'off', 'inactive', 'clear'}:
        return False
    raise ValueError('Value cannot be converted to Boolean')


def convert_profile_telemetry_value(field: dict, value):
    """
    Convert one TTN decoded value according to the profile field's
    declared data type, range and precision.
    """
    field_key = str(field.get('field_key') or '').strip()
    data_type = str(field.get('data_type') or 'number').strip().lower()
    nullable = bool(field.get('nullable'))
    if value is None:
        if nullable:
            return None
        raise ValueError(f'{field_key} cannot be null')
    if data_type == 'number':
        if isinstance(value, bool):
            raise ValueError(f'{field_key} must be numeric')
        try:
            converted = float(value)
        except Exception:
            raise ValueError(f'{field_key} must be numeric')
        if converted != converted or converted in {float('inf'), float('-inf')
            }:
            raise ValueError(f'{field_key} must be a finite number')
    elif data_type == 'integer':
        if isinstance(value, bool):
            raise ValueError(f'{field_key} must be an integer')
        try:
            numeric_value = float(value)
        except Exception:
            raise ValueError(f'{field_key} must be an integer')
        if numeric_value != numeric_value or numeric_value in {float('inf'),
            float('-inf')} or not numeric_value.is_integer():
            raise ValueError(f'{field_key} must be an integer')
        converted = int(numeric_value)
    elif data_type == 'boolean':
        converted = convert_profile_boolean_value(value)
    elif data_type == 'string':
        if isinstance(value, (dict, list)):
            raise ValueError(f'{field_key} must be a string')
        converted = str(value)
    else:
        raise ValueError(f'Unsupported profile data type: {data_type}')
    if data_type in {'number', 'integer'} and converted is not None:
        min_value = field.get('min_value')
        max_value = field.get('max_value')
        if min_value is not None and converted < float(min_value):
            raise ValueError(
                f'{field_key} is below its minimum value of {min_value}')
        if max_value is not None and converted > float(max_value):
            raise ValueError(
                f'{field_key} exceeds its maximum value of {max_value}')
        precision_digits = field.get('precision_digits')
        if precision_digits is not None and data_type == 'number':
            converted = round(converted, int(precision_digits))
    return converted


CAYENNE_CHANNEL_PREFIX_MAP = {
    'temperature': 'temperature',
    'relative_humidity': 'humidity',
    'humidity': 'humidity',
    'presence': 'motion',
    'motion': 'motion',
    'alarm': 'alarm',
    'digital_in': 'digital_in',
    'digital_out': 'digital_out',
    'analog_in': 'analog_in',
    'analog_out': 'analog_out',
    'voltage': 'voltage',
    'current': 'current',
    'power': 'power',
    'luminosity': 'lux',
    'lux': 'lux',
    'barometric_pressure': 'pressure',
    'pressure': 'pressure',
    'co2': 'co2',
    'concentration': 'co2',
    'battery': 'battery',
}


def validate_decoded_payload_against_profile(profile: dict, decoded_payload):
    """
    Validate TTN decoded_payload against the telemetry fields defined
    by the assigned sensor profile.

    Unexpected sensor fields are excluded and reported as warnings.
    Approved system fields and Cayenne LPP channels are preserved.
    """
    errors = []
    warnings = []
    telemetry = {}
    unknown_fields = []
    missing_fields = []
    profile_code = str(profile.get('profile_code') or '').strip().upper()
    profile_version = int(profile.get('profile_version') or 0)
    payload_version = int(profile.get('payload_version') or 0)
    if not isinstance(decoded_payload, dict):
        return {'valid': False, 'profile_code': profile_code,
            'profile_version': profile_version, 'payload_version':
            payload_version, 'telemetry': {}, 'errors': [
            'TTN decoded_payload must be a JSON object'], 'warnings': [],
            'unknown_fields': [], 'missing_fields': []}
    fields = profile.get('fields', [])
    fields_by_key = {str(field.get('field_key') or '').strip().lower():
        field for field in fields if str(field.get('field_key') or '').strip()}
    normalized_payload = {str(key).strip().lower(): value for key, value in
        decoded_payload.items()}

    # Recognize Cayenne LPP dynamic channels (e.g. temperature_1 -> temperature)
    cayenne_channel_fields = {}
    for raw_key, raw_val in list(normalized_payload.items()):
        m = re.match(r'^([a-zA-Z_]+)_(\d+)$', raw_key)
        if m:
            prefix = m.group(1).lower()
            std_key = CAYENNE_CHANNEL_PREFIX_MAP.get(prefix, prefix)
            cayenne_channel_fields[raw_key] = raw_val
            if std_key not in normalized_payload:
                normalized_payload[std_key] = raw_val

    for field_key, field in fields_by_key.items():
        required = bool(field.get('required'))
        if field_key not in normalized_payload:
            if required:
                missing_fields.append(field_key)
                errors.append(
                    f'Required telemetry field is missing: {field_key}')
            continue
        try:
            telemetry[field_key] = convert_profile_telemetry_value(field,
                normalized_payload[field_key])
        except ValueError as exc:
            errors.append(str(exc))

    for system_key in PROFILE_SYSTEM_TELEMETRY_FIELDS:
        if system_key in normalized_payload:
            system_value = normalized_payload[system_key]
            if isinstance(system_value, (dict, list)):
                warnings.append(f'Ignored invalid system field: {system_key}')
                continue
            telemetry[system_key] = system_value

    # Preserve all decoded Cayenne LPP channels directly into telemetry
    telemetry.update({
        ch_key: ch_val for ch_key, ch_val in cayenne_channel_fields.items()
        if isinstance(ch_val, (int, float, bool, str))
    })

    # For universal Cayenne LPP profile, include any other valid numeric/bool fields
    if profile_code == 'CAYENNE_LPP_V1':
        for k, v in normalized_payload.items():
            if k not in telemetry and isinstance(v, (int, float, bool, str)):
                telemetry[k] = v

    allowed_keys = set(fields_by_key.keys()) | PROFILE_SYSTEM_TELEMETRY_FIELDS | set(cayenne_channel_fields.keys()) | set(telemetry.keys())
    for payload_key in normalized_payload:
        if payload_key not in allowed_keys:
            unknown_fields.append(payload_key)
    if unknown_fields:
        warnings.append(
            'Ignored telemetry fields not defined in the profile: ' + ', '.
            join(sorted(unknown_fields)))
    return {'valid': len(errors) == 0, 'profile_code': profile_code,
        'profile_version': profile_version, 'payload_version':
        payload_version, 'expected_fields': sorted(fields_by_key.keys()),
        'telemetry': telemetry, 'errors': errors, 'warnings': warnings,
        'unknown_fields': sorted(unknown_fields), 'missing_fields': sorted(
        missing_fields)}


def profile_alarm_numeric_value(value, field_key: str):
    """
    Convert one telemetry or threshold value into a finite number.
    """
    if isinstance(value, bool):
        raise ValueError(f'{field_key} cannot be evaluated as a number')
    try:
        numeric_value = float(value)
    except Exception:
        raise ValueError(f'{field_key} cannot be evaluated as a number')
    if numeric_value != numeric_value or numeric_value in {float('inf'),
        float('-inf')}:
        raise ValueError(f'{field_key} must be a finite number')
    return numeric_value


def profile_alarm_values_equal(actual_value, expected_value):
    """
    Compare profile alarm values while respecting Boolean, numeric
    and text values.
    """
    if isinstance(expected_value, bool):
        try:
            return convert_profile_boolean_value(actual_value
                ) == expected_value
        except ValueError:
            return False
    if isinstance(expected_value, (int, float)) and not isinstance(
        expected_value, bool):
        try:
            return float(actual_value) == float(expected_value)
        except Exception:
            return False
    return str(actual_value) == str(expected_value)


def profile_alarm_expected_value(rule: dict):
    """
    Select the comparison value for == and != rules.
    """
    if rule.get('expected_boolean') is not None:
        return rule['expected_boolean']
    if rule.get('threshold_value') is not None:
        return rule['threshold_value']
    if rule.get('expected_text') is not None:
        return rule['expected_text']
    raise ValueError('Equality rule has no comparison value')


def profile_alarm_rule_matches(rule: dict, actual_value):
    """
    Evaluate one profile rule against one telemetry value.
    """
    field_key = str(rule.get('field_key') or '').strip().lower()
    operator = str(rule.get('operator') or '').strip().lower()
    threshold_value = rule.get('threshold_value')
    threshold_value_2 = rule.get('threshold_value_2')
    if operator in {'>', '>=', '<', '<=', 'between', 'outside'}:
        actual_number = profile_alarm_numeric_value(actual_value, field_key)
        if threshold_value is None:
            raise ValueError(f'Rule for {field_key} has no threshold')
        threshold_number = profile_alarm_numeric_value(threshold_value,
            field_key)
        if operator == '>':
            return actual_number > threshold_number
        if operator == '>=':
            return actual_number >= threshold_number
        if operator == '<':
            return actual_number < threshold_number
        if operator == '<=':
            return actual_number <= threshold_number
        if threshold_value_2 is None:
            raise ValueError(
                f'Rule for {field_key} requires a second threshold')
        threshold_number_2 = profile_alarm_numeric_value(threshold_value_2,
            field_key)
        low = min(threshold_number, threshold_number_2)
        high = max(threshold_number, threshold_number_2)
        if operator == 'between':
            return low <= actual_number <= high
        if operator == 'outside':
            return (actual_number < low or actual_number > high)
    if operator in {'==', '!='}:
        expected_value = profile_alarm_expected_value(rule)
        values_equal = profile_alarm_values_equal(actual_value, expected_value)
        if operator == '==':
            return values_equal
        return not values_equal
    if operator == 'contains':
        expected_text = rule.get('expected_text')
        if expected_text is None:
            raise ValueError(
                f'Contains rule for {field_key} has no expected text')
        return str(expected_text).lower() in str(actual_value).lower()
    raise ValueError(f'Unsupported alarm operator: {operator}')


def format_profile_alarm_message(profile: dict, rule: dict, actual_value):
    """
    Render a profile alarm message using safe template values.
    """
    message_template = str(rule.get('message_template') or rule.get(
        'alarm_type') or rule.get('rule_code') or 'Sensor alarm').strip()
    template_values = ProfileAlarmTemplateValues({'profile': profile.get(
        'profile_name'), 'profile_code': profile.get('profile_code'),
        'profile_version': profile.get('profile_version'), 'rule_code':
        rule.get('rule_code'), 'alarm_type': rule.get('alarm_type'),
        'severity': rule.get('severity'), 'field': rule.get('field_key'),
        'field_key': rule.get('field_key'), 'value': actual_value,
        'threshold': rule.get('threshold_value'), 'threshold_value': rule.
        get('threshold_value'), 'threshold2': rule.get('threshold_value_2'),
        'threshold_value_2': rule.get('threshold_value_2'),
        'expected_boolean': rule.get('expected_boolean'), 'expected_text':
        rule.get('expected_text')})
    try:
        return message_template.format_map(template_values)
    except Exception:
        return message_template


def evaluate_profile_alarm_rules(profile: dict, telemetry: dict):
    """
    Evaluate every enabled alarm rule stored in a sensor profile.

    This is a stateless evaluator. Debounce, cooldown and automatic
    resolution will be handled when the engine is connected to the
    live webhook.
    """
    if not isinstance(profile, dict):
        raise ValueError('A sensor-profile dictionary is required')
    if not isinstance(telemetry, dict):
        raise ValueError('Telemetry must be a dictionary')
    rules = profile.get('rules', [])
    normalized_telemetry = {str(key).strip().lower(): value for key, value in
        telemetry.items()}
    matched_alarms = []
    messages = []
    warnings = []
    evaluation_errors = []
    evaluated_rules = 0
    skipped_rules = 0
    for rule in rules:
        if not bool(rule.get('enabled')):
            skipped_rules += 1
            continue
        rule_code = str(rule.get('rule_code') or '').strip()
        field_key = str(rule.get('field_key') or '').strip().lower()
        if not field_key:
            evaluation_errors.append({'rule_code': rule_code, 'error':
                'Alarm rule has no field_key'})
            continue
        if field_key not in normalized_telemetry:
            skipped_rules += 1
            warnings.append({'rule_code': rule_code, 'field_key': field_key,
                'warning': 'Telemetry field was not reported'})
            continue
        actual_value = normalized_telemetry[field_key]
        evaluated_rules += 1
        try:
            matched = profile_alarm_rule_matches(rule, actual_value)
        except Exception as exc:
            evaluation_errors.append({'rule_code': rule_code, 'field_key':
                field_key, 'error': str(exc)})
            continue
        if not matched:
            continue
        message = format_profile_alarm_message(profile, rule, actual_value)
        alarm = {'rule_id': rule.get('id'), 'rule_code': rule_code,
            'profile_id': profile.get('id'), 'profile_code': profile.get(
            'profile_code'), 'profile_version': profile.get(
            'profile_version'), 'field_key': field_key, 'actual_value':
            actual_value, 'operator': rule.get('operator'),
            'threshold_value': rule.get('threshold_value'),
            'threshold_value_2': rule.get('threshold_value_2'),
            'expected_boolean': rule.get('expected_boolean'),
            'expected_text': rule.get('expected_text'), 'severity': str(
            rule.get('severity') or 'warning').strip().lower(),
            'alarm_type': str(rule.get('alarm_type') or 'sensor_alarm').
            strip().lower(), 'message': message, 'debounce_seconds': int(
            rule.get('debounce_seconds') or 0), 'cooldown_seconds': int(
            rule.get('cooldown_seconds') or 0), 'auto_resolve': bool(rule.
            get('auto_resolve'))}
        matched_alarms.append(alarm)
        if message not in messages:
            messages.append(message)
    severity_order = {'critical': 1, 'warning': 2, 'info': 3}
    matched_alarms.sort(key=lambda alarm: (severity_order.get(alarm[
        'severity'], 99), alarm['rule_code']))
    return {'profile_code': profile.get('profile_code'), 'profile_version':
        profile.get('profile_version'), 'rule_count': len(rules),
        'evaluated_rule_count': evaluated_rules, 'skipped_rule_count':
        skipped_rules, 'alarm_count': len(matched_alarms), 'alarm_active':
        bool(matched_alarms), 'alarms': matched_alarms, 'messages':
        messages, 'warnings': warnings, 'evaluation_errors': evaluation_errors}


def profile_alarm_runtime_now(now: (datetime | None)=None):
    """
    Return a naive UTC datetime and a SQLite-friendly text value.
    """
    if now is None:
        value = datetime.now(timezone.utc).replace(tzinfo=None)
    elif isinstance(now, datetime):
        value = now
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        raise ValueError('now must be a datetime or None')
    value = value.replace(microsecond=0)
    return value, value.isoformat(sep=' ')


def parse_profile_alarm_timestamp(value):
    """
    Parse alarm runtime timestamps stored in SQLite.
    """
    if not value:
        return None
    text = str(value).strip()
    text = text.removesuffix('Z')
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def insert_profile_rule_alarm_history(conn: sqlite3.Connection, device:
    dict, profile: dict, alarm: dict, telemetry: dict, triggered_at: str):
    """
    Insert one structured alarm-history entry for a matched profile
    rule and return its generated database ID.
    """
    expected_boolean = alarm.get('expected_boolean')
    if expected_boolean is not None:
        expected_boolean = 1 if expected_boolean else 0
    cursor = conn.execute(
        """
        INSERT INTO alarm_history(
            device_id,
            node_type,
            building,
            floor,
            room,

            alarm_type,
            alarm_message,
            telemetry,
            triggered_at,

            profile_id,
            profile_code,
            profile_version,

            rule_id,
            rule_code,
            severity,
            field_key,

            actual_value_json,
            operator,
            threshold_value,
            threshold_value_2,
            expected_boolean,
            expected_text,

            source,
            auto_resolved,
            resolved_reason
        )
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, 0, NULL
        )
        """
        , (device['device_id'], profile['node_type'], device['building'],
        device['floor'], device['room'], alarm['alarm_type'], alarm[
        'message'], json.dumps(telemetry, ensure_ascii=False, sort_keys=
        True), triggered_at, profile['id'], profile['profile_code'],
        profile['profile_version'], alarm.get('rule_id'), alarm['rule_code'
        ], alarm['severity'], alarm['field_key'], json.dumps(alarm.get(
        'actual_value'), ensure_ascii=False), alarm.get('operator'), alarm.
        get('threshold_value'), alarm.get('threshold_value_2'),
        expected_boolean, alarm.get('expected_text'), 'sensor_profile_rule'))
    return cursor.lastrowid


def build_device_alarm_profile(conn: sqlite3.Connection, device_id: str,
    profile: dict) -> dict:
    """
    Return the profile as this one device's alarms should be judged by.

    Thresholds belong to the profile, which is what makes a fleet of
    identical sensors manageable. A device that genuinely lives somewhere
    unusual can patch individual rules, and this is where the two are
    reconciled -- before evaluation rather than inside it, so the
    evaluator stays a pure function of the rules handed to it and so the
    alarm history records the threshold that actually fired.

    The profile passed in is never mutated: it is loaded once per uplink
    and several consumers read it afterwards.
    """
    overrides = load_overrides(conn, device_id)
    if not overrides:
        return profile

    merged_rules, stale_rule_codes = apply_overrides(
        profile.get('rules') or [], overrides)
    if stale_rule_codes:
        # Left behind by a profile change. Inert by design -- a device
        # must keep reporting through a reassignment -- and surfaced in
        # the UI so someone can clear them deliberately.
        logger.info(
            'Device %s has alarm overrides matching no rule in %s: %s',
            device_id, profile.get('profile_code'),
            ', '.join(stale_rule_codes))

    patched_profile = dict(profile)
    patched_profile['rules'] = merged_rules
    return patched_profile

def process_profile_alarm_runtime(conn: sqlite3.Connection, device: dict,
    profile: dict, telemetry: dict, evaluation: dict, now: (datetime | None
    )=None, commit: bool = True):
    """
    Apply debounce, cooldown and automatic resolution to the
    stateless result returned by evaluate_profile_alarm_rules().

    One persistent state row is maintained for every device/rule.
    """
    now_datetime, now_text = profile_alarm_runtime_now(now)
    matched_by_rule_code = {alarm['rule_code']: alarm for alarm in
        evaluation.get('alarms', [])}
    triggered_alarms = []
    auto_resolved_alarm_ids = []
    pending_debounce_rule_codes = []
    cooldown_suppressed_rule_codes = []
    already_active_rule_codes = []
    active_condition_messages = []
    broadcast_events = []
    for rule in profile.get('rules', []):
        if not bool(rule.get('enabled')):
            continue
        rule_code = str(rule.get('rule_code') or '').strip()
        if not rule_code:
            continue
        alarm = matched_by_rule_code.get(rule_code)
        state_row = conn.execute(
            """
            SELECT *
            FROM profile_alarm_runtime_state
            WHERE device_id = ?
              AND rule_code = ?
            LIMIT 1
            """
            , (device['device_id'], rule_code)).fetchone()
        state = dict(state_row) if state_row else None
        if alarm is not None:
            active_condition_messages.append(alarm['message'])
            actual_value_json = json.dumps(alarm.get('actual_value'),
                ensure_ascii=False)
            condition_was_active = bool(state and state.get(
                'is_condition_active'))
            if not state:
                conn.execute(
                    """
                    INSERT INTO
                    profile_alarm_runtime_state(
                        device_id,

                        profile_id,
                        profile_code,
                        profile_version,

                        rule_id,
                        rule_code,

                        is_condition_active,
                        first_matched_at,
                        last_evaluated_at,
                        last_matched_at,

                        occurrence_count,
                        last_value_json,
                        updated_at
                    )
                    VALUES (
                        ?,
                        ?, ?, ?,
                        ?, ?,
                        1,
                        ?, ?, ?,
                        1,
                        ?,
                        ?
                    )
                    """
                    , (device['device_id'], profile['id'], profile[
                    'profile_code'], profile['profile_version'], alarm.get(
                    'rule_id'), rule_code, now_text, now_text, now_text,
                    actual_value_json, now_text))
                first_matched_at = now_datetime
                last_triggered_at = None
                active_alarm_history_id = None
            else:
                if condition_was_active:
                    first_matched_at = parse_profile_alarm_timestamp(state.
                        get('first_matched_at')) or now_datetime
                    occurrence_increment = 0
                else:
                    first_matched_at = now_datetime
                    occurrence_increment = 1
                last_triggered_at = parse_profile_alarm_timestamp(state.get
                    ('last_triggered_at'))
                active_alarm_history_id = state.get('active_alarm_history_id')
                if active_alarm_history_id:
                    history_row = conn.execute(
                        """
                        SELECT resolved
                        FROM alarm_history
                        WHERE id = ?
                        LIMIT 1
                        """
                        , (active_alarm_history_id,)).fetchone()
                    if not history_row or bool(history_row['resolved']):
                        active_alarm_history_id = None
                conn.execute(
                    """
                    UPDATE
                    profile_alarm_runtime_state
                    SET
                        profile_id = ?,
                        profile_code = ?,
                        profile_version = ?,

                        rule_id = ?,

                        is_condition_active = 1,

                        first_matched_at = ?,
                        last_evaluated_at = ?,
                        last_matched_at = ?,

                        active_alarm_history_id = ?,

                        occurrence_count =
                            occurrence_count + ?,

                        last_value_json = ?,
                        updated_at = ?
                    WHERE device_id = ?
                      AND rule_code = ?
                    """
                    , (profile['id'], profile['profile_code'], profile[
                    'profile_version'], alarm.get('rule_id'),
                    first_matched_at.isoformat(sep=' '), now_text, now_text,
                    active_alarm_history_id, occurrence_increment,
                    actual_value_json, now_text, device['device_id'],
                    rule_code))
            debounce_seconds = max(0, int(alarm.get('debounce_seconds') or 0))
            cooldown_seconds = max(0, int(alarm.get('cooldown_seconds') or 0))
            matched_duration = (now_datetime - first_matched_at).total_seconds(
                )
            debounce_passed = matched_duration >= debounce_seconds
            if last_triggered_at is None:
                cooldown_passed = True
            else:
                cooldown_passed = (now_datetime - last_triggered_at
                    ).total_seconds() >= cooldown_seconds
            if active_alarm_history_id:
                already_active_rule_codes.append(rule_code)
                continue
            if not debounce_passed:
                pending_debounce_rule_codes.append(rule_code)
                continue
            if not cooldown_passed:
                cooldown_suppressed_rule_codes.append(rule_code)
                continue
            alarm_history_id = insert_profile_rule_alarm_history(conn=conn,
                device=device, profile=profile, alarm=alarm, telemetry=
                telemetry, triggered_at=now_text)
            broadcast_events.append({
                'type': 'ALARM',
                'device_id': device['device_id'],
                'device_name': device.get('label') or device.get('name') or device.get('device_id'),
                'rule': alarm.get('rule_code', 'Unknown Rule'),
                'telemetry': telemetry
            })
            conn.execute(
                """
                UPDATE
                profile_alarm_runtime_state
                SET
                    active_alarm_history_id = ?,
                    last_triggered_at = ?,
                    updated_at = ?
                WHERE device_id = ?
                  AND rule_code = ?
                """
                , (alarm_history_id, now_text, now_text, device['device_id'
                ], rule_code))
            triggered_alarm = dict(alarm)
            triggered_alarm['alarm_history_id'] = alarm_history_id
            triggered_alarms.append(triggered_alarm)
            continue
        if not state:
            continue
        was_active = bool(state.get('is_condition_active'))
        active_alarm_history_id = state.get('active_alarm_history_id')
        if was_active and active_alarm_history_id and bool(rule.get(
            'auto_resolve')):
            conn.execute(
                """
                UPDATE alarm_history
                SET
                    resolved = 1,
                    resolved_by = 'system',
                    resolved_at = ?,
                    auto_resolved = 1,
                    resolved_reason =
                        'profile_rule_condition_cleared'
                WHERE id = ?
                  AND resolved = 0
                """
                , (now_text, active_alarm_history_id))
            auto_resolved_alarm_ids.append(active_alarm_history_id)
        conn.execute(
            """
            UPDATE
            profile_alarm_runtime_state
            SET
                is_condition_active = 0,
                first_matched_at = NULL,
                last_evaluated_at = ?,
                last_cleared_at = ?,
                active_alarm_history_id = NULL,
                updated_at = ?
            WHERE device_id = ?
              AND rule_code = ?
            """
            , (now_text, now_text, now_text, device['device_id'], rule_code))
    if commit:
        conn.commit()
    unique_active_messages = []
    for message in active_condition_messages:
        if message and message not in unique_active_messages:
            unique_active_messages.append(message)
    return {'profile_code': profile.get('profile_code'), 'profile_version':
        profile.get('profile_version'), 'current_condition_active': bool(
        active_condition_messages), 'current_active_messages':
        unique_active_messages, 'triggered_alarm_count': len(
        triggered_alarms), 'triggered_alarms': triggered_alarms,
        'auto_resolved_alarm_ids': auto_resolved_alarm_ids,
        'pending_debounce_rule_codes': pending_debounce_rule_codes,
        'cooldown_suppressed_rule_codes': cooldown_suppressed_rule_codes,
        'already_active_rule_codes': already_active_rule_codes,
        'broadcast_events': broadcast_events}


def get_active_profile_alarm_records(conn: sqlite3.Connection, device_id: str):
    """
    Return profile alarms that currently have an active condition
    and an unresolved alarm-history record.

    A condition waiting for debounce or suppressed by cooldown is
    not treated as an active alarm.
    """
    rows = conn.execute(
        """
        SELECT
            history.id AS alarm_history_id,

            history.profile_id,
            history.profile_code,
            history.profile_version,

            history.rule_id,
            history.rule_code,

            history.severity,
            history.alarm_type,
            history.alarm_message,
            history.field_key,

            history.actual_value_json,
            history.operator,
            history.threshold_value,
            history.threshold_value_2,

            history.triggered_at,

            runtime.last_matched_at,
            runtime.occurrence_count

        FROM profile_alarm_runtime_state AS runtime

        JOIN alarm_history AS history
          ON history.id =
             runtime.active_alarm_history_id

        WHERE runtime.device_id = ?
          AND runtime.is_condition_active = 1
          AND history.resolved = 0

        ORDER BY
            CASE history.severity
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                WHEN 'info' THEN 3
                ELSE 4
            END,
            history.id
        """
        , (device_id,)).fetchall()
    active_alarms = []
    for row in rows:
        alarm = dict(row)
        try:
            alarm['actual_value'] = json.loads(alarm.get('actual_value_json'))
        except Exception:
            alarm['actual_value'] = alarm.get('actual_value_json')
        active_alarms.append(alarm)
    return active_alarms


def process_ttn_webhook_background(data: dict):
    # Deliberately synchronous. Starlette runs sync background tasks in a
    # threadpool; as an `async def` this ran directly on the event loop while
    # doing blocking sqlite, ThingsBoard HTTP and SMTP calls, so a single slow
    # uplink stalled every other request and the alarm websocket.
    """
    Background worker for processing TTN uplinks.
    """
    conn = None
    device_id = ''
    try:
        logger.info('TTN webhook received')
        end_device_ids = data.get('end_device_ids') or {}
        device_id = str(end_device_ids.get('device_id') or '').strip()
        if not device_id:
            return {'status': 'rejected_invalid_ttn_envelope', 'stage':
                'device_identity', 'errors': [
                'TTN webhook is missing end_device_ids.device_id'],
                'telemetry_stored': False, 'thingsboard_sent': False}
        uplink_message = data.get('uplink_message') or {}
        decoded_payload = uplink_message.get('decoded_payload')
        if decoded_payload is None:
            decoded_payload = {}
        conn = db()
        context = load_device_profile_context_for_webhook(conn, device_id, raw_envelope=data)
        device = context['device']

        # A nickname uplink carries no telemetry: it exists so an installer can
        # tell fifty identical boards apart. Cayenne cannot decode it, so it
        # arrives with frm_payload and no decoded_payload, and it is handled
        # here and returned rather than being run through profile validation.
        if is_label_port(uplink_message.get('f_port')):
            return apply_device_label_uplink(
                conn, device, uplink_message.get('frm_payload'))
        # The DevEUI is the stable radio identity. If TTN's human-readable
        # device ID changed, continue under the existing backend record.
        device_id = device['device_id']
        profile = context['profile']
        node_type = str(profile.get('node_type') or '').strip().lower()
        capabilities = profile_unique_string_list(profile.get(
            'capabilities', []))
        validation = validate_decoded_payload_against_profile(profile,
            decoded_payload)
        learn_profile_fields(conn, profile, validation.get('telemetry') or {})
        if not validation['valid']:
            logger.info('Profile telemetry validation rejected: %s', validation[
                'errors'])
            return {'status': 'rejected_invalid_profile_payload',
                'device_id': device_id, 'profile_code': validation[
                'profile_code'], 'profile_version': validation[
                'profile_version'], 'payload_version': validation[
                'payload_version'], 'errors': validation['errors'],
                'warnings': validation['warnings'], 'missing_fields':
                validation['missing_fields'], 'unknown_fields': validation[
                'unknown_fields'], 'telemetry_stored': False,
                'thingsboard_sent': False}
        telemetry = dict(validation['telemetry'])
        reported_payload_version = telemetry.get('payload_version')
        if reported_payload_version is not None:
            try:
                reported_payload_version = int(reported_payload_version)
            except Exception:
                return {'status': 'rejected_invalid_profile_payload',
                    'device_id': device_id, 'profile_code': profile[
                    'profile_code'], 'errors': [
                    'Reported payload_version must be an integer'],
                    'telemetry_stored': False, 'thingsboard_sent': False}
            expected_payload_version = int(profile.get('payload_version') or 0)
            if reported_payload_version != expected_payload_version:
                return {'status': 'rejected_payload_version_mismatch',
                    'device_id': device_id, 'profile_code': profile[
                    'profile_code'], 'reported_payload_version':
                    reported_payload_version, 'expected_payload_version':
                    expected_payload_version, 'telemetry_stored': False,
                    'thingsboard_sent': False}
        telemetry['profile_code'] = profile['profile_code']
        telemetry['profile_version'] = int(profile['profile_version'])
        telemetry['payload_version'] = int(profile['payload_version'])
        telemetry['node_type'] = node_type
        telemetry = enrich_battery_telemetry(telemetry)
        telemetry = enrich_signal_telemetry(data, telemetry)
        # Single source of truth: whether a device is commissioned decides
        # whether alarms fire at all, so it must not drift from the definition
        # the UI and the acceptance tooling use.
        commissioned = build_device_state(device)['commissioned']
        # Both the evaluator and the runtime processor walk the rule list,
        # and both must see the same one, or a rule muted for this device
        # would still have its debounce and auto-resolve state advanced.
        alarm_profile = build_device_alarm_profile(conn, device_id, profile)
        profile_alarm_evaluation = evaluate_profile_alarm_rules(
            alarm_profile, telemetry)
        if commissioned:
            profile_alarm_runtime = process_profile_alarm_runtime(conn=conn,
                device=device, profile=alarm_profile, telemetry=telemetry,
                evaluation=profile_alarm_evaluation, commit=False)
            active_profile_alarms = get_active_profile_alarm_records(conn,
                device_id)
        else:
            profile_alarm_runtime = {
                'monitoring_state': 'discovery',
                'suppressed_until_commissioned': True,
                'triggered_alarm_count': 0,
                'triggered_alarms': [],
                'broadcast_events': [],
            }
            active_profile_alarms = []
        active_profile_messages = []
        for alarm in active_profile_alarms:
            message = str(alarm.get('alarm_message') or '').strip()
            if message and message not in active_profile_messages:
                active_profile_messages.append(message)
        system_alarms = []
        if commissioned:
            system_alarms.extend(check_battery_alarms(telemetry))
            system_alarms.extend(check_signal_alarms(telemetry))
        unique_system_alarms = []
        for alarm in system_alarms:
            message = str(alarm or '').strip()
            if message and message not in unique_system_alarms:
                unique_system_alarms.append(message)
        system_alarms = unique_system_alarms
        newly_triggered_system_alarms = []
        active_system_rows = []
        if commissioned:
            active_system_rows = conn.execute(
                """SELECT id, alarm_message FROM alarm_history
                   WHERE device_id = ? AND alarm_type = 'system' AND resolved = 0""",
                (device_id,)
            ).fetchall()
        active_system_map = {row['alarm_message']: row['id'] for row in active_system_rows}

        for system_alarm in system_alarms:
            if system_alarm not in active_system_map:
                save_alarm_history(conn=conn, device_id=device_id, node_type=
                    node_type, building=device['building'], floor=device[
                    'floor'], room=device['room'], alarm_message=system_alarm,
                    telemetry=telemetry, alarm_type='system', commit=False)
                newly_triggered_system_alarms.append(system_alarm)

        for active_msg, active_id in active_system_map.items():
            if active_msg not in system_alarms:
                conn.execute(
                    """UPDATE alarm_history
                       SET resolved = 1, resolved_by = 'system', resolved_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (active_id,)
                )
        current_alarm_messages = []
        for message in (active_profile_messages + system_alarms):
            if message and message not in current_alarm_messages:
                current_alarm_messages.append(message)
        telemetry['alarm_active'] = bool(current_alarm_messages)
        telemetry['alarm_message'] = ', '.join(current_alarm_messages
            ) if current_alarm_messages else 'OK'
        telemetry['profile_alarm_active_count'] = len(active_profile_alarms)
        telemetry['profile_alarm_rule_codes'] = [alarm['rule_code'] for
            alarm in active_profile_alarms]
        # A valid uplink proves connectivity even while a device is still in
        # discovery. Clear any stale offline record without enabling new
        # operational alarms before commissioning.
        conn.execute(
            """
            UPDATE alarm_history
            SET resolved = 1,
                resolved_by = 'system',
                resolved_at = CURRENT_TIMESTAMP,
                resolved_reason = 'Device telemetry resumed'
            WHERE device_id = ?
              AND alarm_type = 'SYSTEM_OFFLINE'
              AND resolved = 0
            """,
            (device_id,),
        )
        save_latest_telemetry(conn, device_id, telemetry, commit=False)
        conn.commit()

        for event in profile_alarm_runtime.get('broadcast_events', []):
            schedule_broadcast(event, client_id=device.get('client_id'),
                site_id=device.get('site_id'), building_id=device.get(
                'building_id'), floor_id=device.get('floor_id'))
        for system_alarm in newly_triggered_system_alarms:
            schedule_broadcast({'type': 'ALARM', 'device_id': device_id,
                'device_name': device.get('label') or device_id, 'rule':
                'system', 'message': system_alarm, 'telemetry': telemetry},
                client_id=device.get('client_id'), site_id=device.get(
                'site_id'), building_id=device.get('building_id'), floor_id
                =device.get('floor_id'))
        newly_triggered_profile_messages = []
        for alarm in profile_alarm_runtime.get('triggered_alarms', []):
            message = str(alarm.get('message') or '').strip()
            if message and message not in newly_triggered_profile_messages:
                newly_triggered_profile_messages.append(message)
        response_base = {'device_id': device_id, 'profile_code': profile[
            'profile_code'], 'profile_version': profile['profile_version'],
            'payload_version': profile['payload_version'], 'node_type':
            node_type, 'capabilities': capabilities, 'decoded_payload':
            decoded_payload, 'validated_telemetry': telemetry,
            'validation_warnings': validation['warnings'], 'ignored_fields':
            validation['unknown_fields'], 'profile_alarm_evaluation': {
            'rule_count': profile_alarm_evaluation['rule_count'],
            'evaluated_rule_count': profile_alarm_evaluation[
            'evaluated_rule_count'], 'matched_rule_count':
            profile_alarm_evaluation['alarm_count'], 'warnings':
            profile_alarm_evaluation['warnings'], 'evaluation_errors':
            profile_alarm_evaluation['evaluation_errors']},
            'profile_alarm_runtime': profile_alarm_runtime,
            'active_profile_alarms': active_profile_alarms, 'system_alarms':
            system_alarms, 'alarms': current_alarm_messages,
            'telemetry_stored': True, 'monitoring_state': 'operational' if
            commissioned else 'discovery', 'alarms_suppressed': not commissioned}
        if LOCAL_TEST_MODE:
            return {'status': 'ok_local_test_profile_validated', **
                response_base, 'thingsboard_sent': False, 'note':
                'LOCAL_TEST_MODE is enabled, so ThingsBoard sending was skipped.'
                }
        thingsboard_sent = False
        thingsboard_error = None
        thingsboard_device_status = None
        thingsboard_attribute_status = None
        try:
            tb_ensure_result = ensure_tb_device_from_profile(device_name=
                device_id, profile=profile)
            tb_device = tb_ensure_result['device']
            thingsboard_device_status = tb_ensure_result['status']
            tb_attribute_result = sync_tb_attributes_from_profile(device_row
                =device, profile=profile, tb_device=tb_device)
            thingsboard_attribute_status = tb_attribute_result['status']
            tb_send_telemetry(tb_device, telemetry)
            thingsboard_sent = True
        except Exception as exc:
            thingsboard_error = str(exc)
            logger.error('Profile-driven ThingsBoard forwarding failed: %s', exc)
        email_alarms = []
        for message in (newly_triggered_profile_messages + newly_triggered_system_alarms):
            if message and message not in email_alarms:
                email_alarms.append(message)
        if email_alarms:
            logger.info(f'NEW ALARM for {device_id}: {email_alarms}')
            send_alarm_email(device_id=device_id, node_type=node_type, room
                =device['room'], alarms=email_alarms, telemetry=telemetry)
        return {'status': 'ok_profile_validated' if thingsboard_sent else
            'ok_profile_validated_thingsboard_failed', **response_base,
            'thingsboard_sent': thingsboard_sent, 'thingsboard_profile':
            profile['tb_device_profile_name'], 'thingsboard_device_status':
            thingsboard_device_status, 'thingsboard_attribute_status':
            thingsboard_attribute_status, 'thingsboard_error':
            thingsboard_error}
    except HTTPException as exc:
        if conn is not None:
            conn.rollback()
        logger.error('TTN webhook profile context rejected: %s', exc.detail)
        return {'status': 'rejected_profile_context', 'device_id': 
            device_id or None, 'http_status': exc.status_code, 'detail':
            exc.detail, 'telemetry_stored': False, 'thingsboard_sent': False}
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        logger.error('TTN webhook error: %s', exc)
        return {'status': 'error', 'device_id': device_id or None,
            'message': str(exc), 'telemetry_stored': False,
            'thingsboard_sent': False}
    finally:
        if conn is not None:
            conn.close()


from app.routers.admin import router as admin_router
from app.routers.alarms import router as alarms_router
from app.routers.audit import router as audit_router
from app.routers.auth import router as auth_router
from app.routers.client_portal import router as client_portal_router
from app.routers.clients import router as clients_router
from app.routers.devices import router as devices_router
from app.routers.enrollment import router as enrollment_router
from app.routers.firmware import router as firmware_router
from app.routers.gateways import router as gateways_router
from app.routers.hierarchy import router as hierarchy_router
from app.routers.integrations import router as integrations_router
from app.routers.pages import router as pages_router
from app.routers.profiles import router as profiles_router
from app.routers.root import router as root_router
from app.routers.telemetry import router as telemetry_router
from app.routers.webhooks import router as webhooks_router
from app.routers.ws import router as ws_router

app.include_router(auth_router)
app.include_router(clients_router)
app.include_router(hierarchy_router)
app.include_router(devices_router)
app.include_router(gateways_router)
app.include_router(profiles_router)
app.include_router(firmware_router)
app.include_router(integrations_router)
app.include_router(telemetry_router)
app.include_router(alarms_router)
app.include_router(client_portal_router)
app.include_router(admin_router)
app.include_router(audit_router)
app.include_router(enrollment_router)
app.include_router(root_router)
app.include_router(pages_router)
app.include_router(webhooks_router)
app.include_router(ws_router)


def check_offline_devices(conn: sqlite3.Connection, now: datetime | None = None):
    if now is None:
        now = datetime.now(timezone.utc)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            t.device_id,
            t.updated_at,
            t.alarm_active,
            t.alarm_message,
            d.client_id,
            d.site_id,
            d.building_id,
            d.floor_id,
            d.node_type,
            d.building,
            d.floor,
            d.room
        FROM device_latest_telemetry t
        LEFT JOIN devices d ON t.device_id = d.device_id
        WHERE d.is_placed = 1
          AND d.floor_id IS NOT NULL
    """)
    rows = cursor.fetchall()
    for row in rows:
        device_id = row["device_id"]
        updated_at_str = row["updated_at"]
        if not updated_at_str:
            continue
        updated_at = parse_datetime_safe(updated_at_str)
        if not updated_at:
            continue

        if now - updated_at > timedelta(hours=24):
            if row["alarm_message"] != "OFFLINE":
                cursor.execute(
                    "UPDATE device_latest_telemetry SET alarm_active=1, alarm_message='OFFLINE' WHERE device_id=?",
                    (device_id,),
                )
                cursor.execute(
                    """
                    INSERT INTO alarm_history (device_id, node_type, building, floor, room, alarm_type, alarm_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        row["node_type"],
                        row["building"],
                        row["floor"],
                        row["room"],
                        "SYSTEM_OFFLINE",
                        "Device has not sent data in 24 hours.",
                    ),
                )
                alert_msg = {
                    "type": "ALARM",
                    "device_id": device_id,
                    "message": "Device Offline (No data in 24h)",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
                schedule_broadcast(
                    alert_msg,
                    client_id=row["client_id"],
                    site_id=row["site_id"],
                    building_id=row["building_id"],
                    floor_id=row["floor_id"],
                )
    conn.commit()


def purge_old_telemetry(conn: sqlite3.Connection):
    try:
        conn.execute("DELETE FROM historical_telemetry WHERE timestamp <= datetime('now', '-30 days')")
        conn.commit()
    except Exception as e:
        logger.error("Failed to purge old telemetry: %s", e)


def run_offline_scan():
    """
    One watchdog pass, executed entirely on a worker thread.

    The connection is opened here rather than by the caller because sqlite3
    connections are bound to the thread that created them.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        check_offline_devices(conn)
        cleanup_expired_sessions(conn)
        purge_old_telemetry(conn)
    finally:
        # Previously outside a finally, so every failed scan leaked a
        # connection -- once every 300s, for the life of the process.
        conn.close()


async def offline_watchdog():
    while True:
        try:
            # Off the event loop: the scan is blocking sqlite work.
            await asyncio.to_thread(run_offline_scan)
        except Exception as e:
            logger.error("Watchdog error: %s", e)
        await asyncio.sleep(300)


