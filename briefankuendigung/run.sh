#!/usr/bin/with-contenv sh
# with-contenv gibt die Umgebung des Containers (u. a. SUPERVISOR_TOKEN und TZ) an Node weiter –
# s6-overlay startet Programme sonst mit leerer Umgebung.
exec node /app/server.mjs
