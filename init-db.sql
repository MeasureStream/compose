-- Database principali
-- SENSORS viene creato automaticamente se POSTGRES_DB=SENSORS
SELECT 'CREATE DATABASE "SETTINGS"'
WHERE NOT EXISTS (
        SELECT
        FROM pg_database
        WHERE datname = 'SETTINGS'
    ) \ gexec
SELECT 'CREATE DATABASE "CALIBRATOR"'
WHERE NOT EXISTS (
        SELECT
        FROM pg_database
        WHERE datname = 'CALIBRATOR'
    ) \ gexec
SELECT 'CREATE DATABASE "SENSORS"'
WHERE NOT EXISTS (
        SELECT
        FROM pg_database
        WHERE datname = 'SENSORS'
    ) \ gexec -- Utente principale
    -- I permessi vengono assegnati se il database esiste
GRANT ALL PRIVILEGES ON DATABASE "SENSORS" TO measurestream_admin;
GRANT ALL PRIVILEGES ON DATABASE "SETTINGS" TO measurestream_admin;
GRANT ALL PRIVILEGES ON DATABASE "CALIBRATOR" TO measurestream_admin;
-- Connetti al database SENSORS e configura permessi per schema public
\ c SENSORS;
-- Assicurati che l'utente abbia i permessi necessari sullo schema public
GRANT ALL PRIVILEGES ON SCHEMA public TO measurestream_admin;
GRANT ALL ON SCHEMA public TO measurestream_admin;