# Laboratorio de ventas con agentes IA

Complemento del libro Crear un equipo de ventas con agentes de inteligencia artificial.
Python 3.11 o posterior. Solo usa la biblioteca estándar; no requiere instalar paquetes.

Este laboratorio procesa solicitudes comerciales locales, emite propuestas de ejemplo,
reserva cupos locales y deja una traza. En modo live, llama a tres funciones de IA por
solicitud normal mediante la Responses API: calificador, consultor y auditor. El programa
comprueba las acciones y obtiene los importes del catálogo. En modo demo sustituye las
llamadas a modelos por reglas de prueba: ese modo NO demuestra inteligencia artificial.

No envía mensajes, no cobra, no conecta un CRM externo y no crea eventos en una agenda
externa. El libro explica cómo añadir esas conexiones. No se presenta como un servicio
listo para publicar en internet: el servidor escucha solo en 127.0.0.1, usa SQLite,
serializa solicitudes y carece de la infraestructura de colas, TLS, control de acceso por
usuario, copias cifradas y supervisión que necesita un despliegue comercial.

## Primera prueba

Abre una terminal dentro de esta carpeta. En Windows, puedes sustituir python por py.

```bash
python --version
python -m unittest -v test_app.py
python http_smoke.py
python app.py demo --db demo.sqlite3
```

La demostración genera un contacto ficticio y un cupo futuro. Devuelve una explicación,
una propuesta, una reserva local y una baja. Las propuestas no son contratos y el contador
sales_paid permanece en cero. La baja cancela salidas pendientes. Puedes repetir demo:
cada ejecución crea identificadores nuevos. Usa una base nueva si quieres empezar vacía.

## Configurar el servidor local

1. Copia .env.example a .env con el administrador de archivos.
2. Genera un token usando el siguiente comando. Pégalo en API_TOKEN dentro de .env.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

3. Conserva API_MODE=demo y DELIVER_OUTBOX=0 durante las primeras pruebas.
4. Registra un contacto ficticio. El permiso de ejemplo no es válido para una persona real.

```bash
python app.py init
python app.py register --id L001 --name "Cliente de prueba" --email prueba@example.com --consent --evidence "Autorización ficticia para pruebas locales"
python app.py serve
```

5. En otra terminal, desde esta misma carpeta:

```bash
python client.py --lead L001 --text "precio AUDIT-150"
python client.py --lead L001 --text "propuesta AUDIT-150"
python app.py metrics
```

El cliente genera event_id y fecha con zona horaria. Para ensayar una reserva, crea un
cupo con fecha futura y desplazamiento horario, consulta GET /slots y envía exactamente
RESERVAR seguido del código. Ejemplo de sintaxis; cambia la fecha según tu prueba:

```bash
python app.py add-slot --id SLOT-PRUEBA --starts-at "2030-01-15T10:00:00-05:00"
python client.py --lead L001 --text "RESERVAR SLOT-PRUEBA"
python client.py --lead L001 --text "BAJA"
```

Un contacto dado de baja no se reactiva mediante mensajes nuevos. El kit no incluye una
función de renovación de permiso. No cambies la base directamente para simular una
autorización real. Para otra prueba usa un contacto ficticio distinto.

## Utilizar modelos reales

Configura en .env API_MODE=live, OPENAI_API_KEY y OPENAI_MODEL. Usa un identificador
habilitado en tu cuenta que soporte el esquema requerido. No se fija un modelo ni una
tarifa: selecciona, prueba y registra la versión elegida. Reinicia el servidor.

Cada solicitud normal puede realizar tres llamadas facturables. No hay reintentos
automáticos del modelo. Una respuesta incompleta, un rechazo, un esquema inválido o un
fallo de red deja el evento en pausa y evita la acción comercial. Un mismo event_id
reutiliza el resultado guardado. Para repetir después de corregir un fallo, revisa primero
el registro y crea deliberadamente un evento nuevo. No uses ese procedimiento para
duplicar acciones que sí se ejecutaron.

Para consultar los últimos códigos de pausa sin mostrar claves ni cuerpos de error:

```bash
python app.py diagnose
```

missing_model_credentials indica variables vacías; model_http_401 indica revisar la clave;
model_http_403 permisos; model_http_429 cuota o límites; model_incomplete una salida no
completada y auditor_veto un rechazo del auditor. No vuelvas a enviar una acción externa
incierta para depurar un fallo de modelo.

store=false limita almacenamiento de estado de la respuesta según la API; no representa
una promesa de retención cero. Antes de enviar datos de clientes, revisa configuración,
contratos y controles del proveedor. Los ensayos entregados usan dobles de API y no
verifican credenciales, disponibilidad de modelos ni comportamiento semántico real.

## Contrato HTTP

Todas las rutas salvo /health exigen Authorization: Bearer seguido de API_TOKEN.
El token es una credencial interna del backend; nunca debe exponerse en una página pública.

| Método | Ruta | Entrada | Resultado |
|---|---|---|---|
| GET | /health | Ninguna | Estado básico |
| GET | /slots | Ninguna | Cupos locales futuros disponibles |
| GET | /metrics | Ninguna | Contadores locales |
| POST | /events | event_id lead_id text occurred_at | Resultado y posible outbox_id |
| POST | /outbox/claim | {} | Una salida reclamada o item null |
| POST | /outbox/result | outbox_id status provider_id | Aceptación o incertidumbre |

Un evento acepta exactamente cuatro campos. occurred_at debe ser ISO 8601 con zona.
No se admiten campos que cambien destinatario, permiso, precio o estado de compra.
El gateway autenticado debe resolver lead_id desde la identidad verificada del canal.

```json
{
  "event_id": "canal-id-unico-001",
  "lead_id": "L001",
  "text": "propuesta AUDIT-150",
  "occurred_at": "2026-10-16T10:00:00-05:00"
}
```

Usa una fecha actual para la prueba. Reenviar exactamente el objeto original devuelve
duplicate=true y la misma propuesta. Cambiar el contenido de un event_id ya usado da
error. Un evento anterior al estado actual no avanza la venta; una baja explícita sigue
aplicándose incluso si llega con retraso.

## Conectar una salida en n8n

Durante pruebas no cambies DELIVER_OUTBOX. La respuesta de /events es una vista de
desarrollo; no la envíes además de consumir la outbox, porque duplicarías mensajes.

Cuando hayas preparado un único consumidor de salida y un permiso válido para el
destinatario de prueba, cambia DELIVER_OUTBOX=1 y reinicia. Esto no envía por sí mismo.
El consumidor solicita POST /outbox/claim con {}, envía una vez mediante el proveedor
configurado y devuelve POST /outbox/result. El kit aplica la política conservadora de
requerir consent_sales con evidencia, incluso para la salida de sus ejemplos.

```json
{"outbox_id":"id-devuelto","status":"accepted","provider_id":"id-real-del-proveedor"}
```

accepted significa que el proveedor aceptó el envío, no entrega al destinatario. Si
no sabes si se aceptó por un timeout, usa uncertain y provider_id vacío. No hay reintento
automático de uncertain ni sending. Reconcílialos con el proveedor antes de cualquier
reenvío. El kit no verifica recibos de entrega. Una baja posterior al claim puede competir
con un envío en curso; el proveedor y tu gateway deben reducir o resolver esa ventana.

El destinatario de claim es un correo del registro local. El adaptador WhatsApp necesita
su propio mapeo autenticado de teléfono y validación de ventana o plantilla; no utiliza
ese correo como teléfono. No añadas destinatarios desde una respuesta del modelo.

## Archivos y pruebas

- app.py contiene el motor y servidor de laboratorio.
- test_app.py contiene 29 pruebas de invariantes y del formato del adaptador.
- http_smoke.py comprueba el contrato HTTP local sin proveedores externos.
- client.py envía un evento de prueba al servidor local.
- config.json contiene el catálogo ficticio.
- prompts_comerciales.json contiene instrucciones ampliables para ocho funciones.
- casos_evaluacion.json contiene escenarios para evaluar tu configuración real.
- .env.example documenta variables sin incluir secretos.

Los prompts adicionales son plantillas. Solo las tres funciones definidas en app.py se
ejecutan en el laboratorio. No existe implementación de prospección web, secuencias
programadas, CRM externo, pagos, voz o agenda externa en el ZIP.

## Antes de un despliegue comercial

Completa los capítulos finales del libro: oferta real y tratamiento tributario, permisos
por finalidad, proveedor de canal, gateway autenticado, cola duradera, CRM, recepción
asincrónica de webhooks, límites de gasto, política de excepciones, supervisión, copias y
recuperación. El servidor HTTP estándar de Python se utiliza aquí para aprender y probar;
para producción, sustituye la capa de servicio por una aplicación y servidor adecuados.
