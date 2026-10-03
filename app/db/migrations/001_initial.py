def up(conn):

    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_alarm_runtime_state(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                            device_id TEXT NOT NULL,
                
                            profile_id INTEGER NOT NULL,
                            profile_code TEXT NOT NULL,
                            profile_version INTEGER NOT NULL,
                
                            rule_id INTEGER,
                            rule_code TEXT NOT NULL,
                
                            is_condition_active INTEGER
                                NOT NULL DEFAULT 0,
                
                            first_matched_at TEXT,
                            last_evaluated_at TEXT,
                            last_matched_at TEXT,
                            last_triggered_at TEXT,
                            last_cleared_at TEXT,
                
                            active_alarm_history_id INTEGER,
                
                            occurrence_count INTEGER
                                NOT NULL DEFAULT 0,
                
                            last_value_json TEXT,
                
                            created_at TEXT
                                NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            updated_at TEXT
                                NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            UNIQUE(
                                device_id,
                                rule_code
                            )
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_catalog(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            sensor_code TEXT NOT NULL UNIQUE,
                            manufacturer TEXT,
                            model TEXT NOT NULL,
                            use_case TEXT,
                            protocol TEXT NOT NULL,
                            default_bus TEXT,
                            default_address TEXT,
                            datasheet_url TEXT,
                            description TEXT,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS firmware_modules(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            module_key TEXT NOT NULL UNIQUE,
                            display_name TEXT NOT NULL,
                            driver_class TEXT NOT NULL,
                            protocol TEXT NOT NULL,
                            library_name TEXT,
                            library_version TEXT,
                            supported_board TEXT NOT NULL DEFAULT 'LILYGO LoRa32',
                            min_firmware_version TEXT,
                            source_file TEXT,
                            notes TEXT,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profiles(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_code TEXT NOT NULL UNIQUE,
                            profile_name TEXT NOT NULL,
                            profile_version INTEGER NOT NULL DEFAULT 1,
                            node_type TEXT NOT NULL,
                            description TEXT,
                
                            capabilities_json TEXT NOT NULL DEFAULT '[]',
                            configuration_schema_json TEXT NOT NULL DEFAULT '{}',
                
                            payload_encoder_key TEXT NOT NULL,
                            payload_version INTEGER NOT NULL DEFAULT 1,
                            f_port INTEGER NOT NULL DEFAULT 1,
                            uplink_interval_seconds INTEGER NOT NULL DEFAULT 60,
                
                            ttn_formatter_code TEXT NOT NULL,
                            ttn_formatter_type TEXT NOT NULL DEFAULT 'javascript',
                
                            tb_device_profile_name TEXT,
                
                            icon_type TEXT NOT NULL DEFAULT 'default',
                            icon_color TEXT,
                
                            status TEXT NOT NULL DEFAULT 'draft',
                            enabled INTEGER NOT NULL DEFAULT 1,
                            is_system INTEGER NOT NULL DEFAULT 0,
                
                            schema_checksum TEXT,
                
                            created_by TEXT,
                            updated_by TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profile_sensors(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                            sensor_id INTEGER NOT NULL,
                            firmware_module_id INTEGER,
                
                            role TEXT NOT NULL DEFAULT 'primary',
                            required INTEGER NOT NULL DEFAULT 1,
                            configuration_json TEXT NOT NULL DEFAULT '{}',
                            display_order INTEGER NOT NULL DEFAULT 0,
                
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE,
                
                            FOREIGN KEY (sensor_id)
                                REFERENCES sensor_catalog(id),
                
                            FOREIGN KEY (firmware_module_id)
                                REFERENCES firmware_modules(id),
                
                            UNIQUE(profile_id, sensor_id, role)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profile_fields(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                
                            field_key TEXT NOT NULL,
                            label TEXT NOT NULL,
                            unit TEXT,
                            data_type TEXT NOT NULL DEFAULT 'number',
                
                            payload_order INTEGER,
                            byte_offset INTEGER,
                            byte_length INTEGER,
                            scale REAL NOT NULL DEFAULT 1.0,
                            signed INTEGER NOT NULL DEFAULT 0,
                            endianness TEXT NOT NULL DEFAULT 'big',
                
                            required INTEGER NOT NULL DEFAULT 1,
                            nullable INTEGER NOT NULL DEFAULT 0,
                
                            display_order INTEGER NOT NULL DEFAULT 0,
                            precision_digits INTEGER,
                
                            visible_floor INTEGER NOT NULL DEFAULT 1,
                            visible_dashboard INTEGER NOT NULL DEFAULT 1,
                
                            min_value REAL,
                            max_value REAL,
                            default_value_json TEXT,
                
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE,
                
                            UNIQUE(profile_id, field_key)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profile_rules(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                
                            rule_code TEXT NOT NULL,
                            field_key TEXT NOT NULL,
                            operator TEXT NOT NULL,
                
                            threshold_value REAL,
                            threshold_value_2 REAL,
                            expected_boolean INTEGER,
                            expected_text TEXT,
                
                            severity TEXT NOT NULL DEFAULT 'warning',
                            alarm_type TEXT NOT NULL,
                            message_template TEXT NOT NULL,
                
                            debounce_seconds INTEGER NOT NULL DEFAULT 0,
                            cooldown_seconds INTEGER NOT NULL DEFAULT 300,
                            auto_resolve INTEGER NOT NULL DEFAULT 1,
                            enabled INTEGER NOT NULL DEFAULT 1,
                
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE,
                
                            UNIQUE(profile_id, rule_code)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profile_versions(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                            version INTEGER NOT NULL,
                
                            snapshot_json TEXT NOT NULL,
                            change_note TEXT,
                            created_by TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE,
                
                            UNIQUE(profile_id, version)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile_firmware_compatibility(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                            firmware_module_id INTEGER NOT NULL,
                
                            min_firmware_version TEXT,
                            max_firmware_version TEXT,
                            hardware_revision TEXT,
                            required_features_json TEXT NOT NULL DEFAULT '[]',
                
                            enabled INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE,
                
                            FOREIGN KEY (firmware_module_id)
                                REFERENCES firmware_modules(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_configuration_history(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                
                            device_id TEXT NOT NULL,
                            profile_id INTEGER,
                            profile_code TEXT,
                            profile_version INTEGER,
                            payload_version INTEGER,
                            firmware_version TEXT,
                
                            configuration_status TEXT NOT NULL DEFAULT 'pending',
                            configuration_payload_json TEXT,
                            configuration_checksum TEXT,
                            error_message TEXT,
                
                            source TEXT NOT NULL DEFAULT 'admin',
                            requested_by TEXT,
                            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            applied_at TEXT,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sensor_profile_test_runs(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            profile_id INTEGER NOT NULL,
                
                            test_type TEXT NOT NULL,
                            input_payload_json TEXT,
                            decoded_telemetry_json TEXT,
                            validation_errors_json TEXT,
                            alarms_generated_json TEXT,
                
                            status TEXT NOT NULL,
                            notes TEXT,
                            created_by TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                
                            FOREIGN KEY (profile_id)
                                REFERENCES sensor_profiles(id)
                                ON DELETE CASCADE
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_capabilities(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            device_id TEXT NOT NULL,
                            capability TEXT NOT NULL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(device_id, capability)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS clients(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            tb_customer_id TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sites(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            client_id INTEGER NOT NULL,
                            name TEXT NOT NULL,
                            campus_image_path TEXT,
                            image_width INTEGER,
                            image_height INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (client_id) REFERENCES clients(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_maps(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            site_id INTEGER NOT NULL,
                            image_path TEXT,
                            image_width INTEGER,
                            image_height INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (site_id) REFERENCES sites(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS gateways(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            gateway_id TEXT UNIQUE NOT NULL,
                            name TEXT NOT NULL,
                            client_id INTEGER,
                            site_id INTEGER,
                            status TEXT DEFAULT 'unknown',
                            last_seen TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (client_id) REFERENCES clients(id),
                            FOREIGN KEY (site_id) REFERENCES sites(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS buildings(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            site_id INTEGER NOT NULL,
                            name TEXT NOT NULL,
                            polygon_points TEXT,
                            x INTEGER,
                            y INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (site_id) REFERENCES sites(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS floors(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            building_id INTEGER NOT NULL,
                            name TEXT NOT NULL,
                            floor_number TEXT,
                            image_path TEXT,
                            image_width INTEGER,
                            image_height INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (building_id) REFERENCES buildings(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL,
                            email TEXT NOT NULL UNIQUE,
                            role TEXT DEFAULT 'client',
                            enabled INTEGER DEFAULT 1,
                            password_hash TEXT,
                            password_salt TEXT,
                            password_iterations INTEGER,
                            password_updated_at TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_access(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            client_id INTEGER,
                            site_id INTEGER,
                            building_id INTEGER,
                            floor_id INTEGER,
                            access_level TEXT DEFAULT 'viewer',
                            can_view_devices INTEGER DEFAULT 1,
                            can_view_gateways INTEGER DEFAULT 1,
                            can_view_alarms INTEGER DEFAULT 1,
                            can_view_telemetry INTEGER DEFAULT 1,
                            can_manage_email_settings INTEGER DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(id),
                            FOREIGN KEY (client_id) REFERENCES clients(id),
                            FOREIGN KEY (site_id) REFERENCES sites(id),
                            FOREIGN KEY (building_id) REFERENCES buildings(id),
                            FOREIGN KEY (floor_id) REFERENCES floors(id)
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        token_hash TEXT NOT NULL UNIQUE,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        expires_at TEXT NOT NULL,
                        revoked_at TEXT,
                        ip_address TEXT,
                        user_agent TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_latest_telemetry(
                            device_id TEXT PRIMARY KEY,
                            telemetry TEXT,
                            alarm_active INTEGER DEFAULT 0,
                            alarm_message TEXT DEFAULT 'OK',
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_telemetry(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            device_id TEXT,
                            telemetry TEXT,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            actor TEXT DEFAULT 'admin',
                            action TEXT NOT NULL,
                            target_type TEXT,
                            target_id TEXT,
                            details TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alarm_recipients(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            email TEXT NOT NULL UNIQUE,
                            enabled INTEGER DEFAULT 1,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alarm_history(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            device_id TEXT NOT NULL,
                            node_type TEXT,
                            building TEXT,
                            floor TEXT,
                            room TEXT,
                            alarm_type TEXT,
                            alarm_message TEXT,
                            telemetry TEXT,
                            triggered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                
                            acknowledged INTEGER DEFAULT 0,
                            acknowledged_by TEXT,
                            acknowledged_at TEXT,
                
                            resolved INTEGER DEFAULT 0,
                            resolved_by TEXT,
                            resolved_at TEXT
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alarm_email_template(
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            subject_template TEXT NOT NULL,
                            body_template TEXT NOT NULL,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS "rooms"(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    floor_id INTEGER NOT NULL,
                    room_name TEXT NOT NULL,
                    polygon_points TEXT NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (floor_id) REFERENCES floors(id)
                )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS "devices"(
                    chip_mac TEXT PRIMARY KEY,
                    device_id TEXT UNIQUE,
                    dev_eui TEXT UNIQUE,
                    join_eui TEXT,
                    app_key TEXT,
                    node_type TEXT,

                    -- Canonical FK-based location (preferred)
                    room_id INTEGER,
                    client_id INTEGER,
                    site_id INTEGER,
                    building_id INTEGER,
                    floor_id INTEGER,

                    -- Denormalised strings kept for legacy compatibility
                    building TEXT,
                    floor TEXT,
                    room TEXT,

                    -- Display metadata
                    label TEXT,
                    x INTEGER,
                    y INTEGER,
                    icon_type TEXT,

                    -- Profile assignment
                    profile_id INTEGER,
                    profile_code TEXT,
                    profile_version INTEGER,

                    -- Payload contract
                    payload_version INTEGER,
                    firmware_version TEXT,

                    -- Configuration tracking
                    configuration_status TEXT DEFAULT 'pending',
                    configuration_error TEXT,
                    configuration_updated_at TEXT,
                    configuration_checksum TEXT,

                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (room_id) REFERENCES rooms(id)
                )
    """)

