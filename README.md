# Vinya Outreach — Manual de uso

Envía correos personalizados a productores agrícolas de todo EE.UU., desde tu Gmail
(apareciendo como luis@hinosinvestments.com), con protecciones contra spam y
cumplimiento de la ley CAN-SPAM.

## Archivos
| Archivo | Para qué sirve |
|---|---|
| `vinya_outreach.py` | El programa |
| `email_template.txt` | El mensaje (edítalo libremente; respeta las palabras entre `{llaves}`) |
| `subjects.txt` | Variantes de asunto, una por línea (se rotan automáticamente) |
| `contacts_template.csv` | Formato de la lista de contactos |
| `sent_log.csv` | Se crea solo: historial de todo lo enviado |
| `suppression.csv` | Se crea solo: quienes pidieron STOP, rebotes, y bajas manuales |

## Configuración (una sola vez)

1. **Contraseña de aplicación de Google** (no tu contraseña normal):
   Cuenta de Google → Seguridad → Verificación en 2 pasos (debe estar activa) →
   Contraseñas de aplicaciones → crea una llamada "Vinya". Copia el código de 16 letras.

2. En Terminal, cada vez que abras una sesión nueva:
   ```bash
   export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
   ```

3. **Dirección postal (obligatoria por ley)**: abre `vinya_outreach.py` y llena
   `"postal_address"`. Debe ser una dirección real donde recibas correo — puede ser
   tu dirección, un P.O. Box, o un buzón virtual. El programa no envía nada sin esto.

4. **Prueba de entregabilidad** (antes del primer envío real):
   - Entra a https://www.mail-tester.com, copia la dirección que te da.
   - Pon esa dirección en un `contacts.csv` de prueba y envía 1 correo.
   - Revisa el puntaje. Debe salir **9/10 o más**, con SPF, DKIM y DMARC en verde.
   - Si sale "via gmail.com" o DMARC falla: en `vinya_outreach.py` cambia
     `"route": "gmail"` por `"route": "godaddy"` y usa la contraseña de tu buzón de
     GoDaddy en `GMAIL_APP_PASSWORD`. Así los correos salen directo desde tu dominio.
   - En GoDaddy → DNS de hinosinvestments.com, confirma que existan registros SPF,
     DKIM y DMARC (el soporte de GoDaddy los activa si faltan).

## Uso diario

```bash
cd "/Volumes/WD easystore/Hinos Investments/vinya_outreach"

python3 vinya_outreach.py preview          # 1. Revisa los correos en /previews
python3 vinya_outreach.py send --dry-run   # 2. Simula sin enviar
python3 vinya_outreach.py send             # 3. Envía (respeta el tope diario)
python3 vinya_outreach.py check-replies    # 4. Detecta respuestas, STOPs y rebotes
python3 vinya_outreach.py stats            # 5. Resumen y tasa de rebote
```

Opciones útiles:
- `send --state CA` — solo contactos de un estado
- `send --limit 5` — solo 5 en esta tanda
- `optout correo@ejemplo.com` — dar de baja a alguien manualmente

## Lista de contactos (`contacts.csv`)
| Columna | Obligatoria | Uso |
|---|---|---|
| business | Sí | Nombre del negocio (va en el saludo y el asunto) |
| email | Sí | Correo |
| contact_name | No | Si existe: "Hi Maria,"; si no: "Hi [Negocio] team," |
| state | No | Para filtrar con `--state` (no aparece en el correo) |
| crop | No | Agrega una línea sobre su cultivo |
| personal_note | No | Una frase escrita por ti para ese negocio (la personalización más efectiva) |
| source | No | De dónde sacaste el contacto — aparece en el pie legal. Debe ser verdad |

## Protecciones anti-spam incluidas
- Tope diario (arranca en 20) y pausas aleatorias de 1.5 a 4 minutos entre correos
- Nunca envía dos veces al mismo correo, ni a dos personas de la misma empresa
- Respeta automáticamente los STOP y los rebotes
- Detiene la tanda completa si el servidor muestra señales de límite o bloqueo
- Solo días hábiles (fin de semana requiere `--force`)
- Texto plano + HTML simple, sin adjuntos, sin acortadores de enlaces, sin imágenes
- Encabezado `List-Unsubscribe` (lo exigen Gmail y Yahoo)
- Asuntos variados, sin palabras típicas de spam ("free", "gratis", "$$$")

## Plan de calentamiento (no lo saltes)
| Semana | Tope diario (`daily_cap`) |
|---|---|
| 1 | 15–20 |
| 2 | 25–30 |
| 3 en adelante | 40 máximo |

Si `stats` muestra tasa de rebote mayor a 2%, detente y limpia la lista (por ejemplo
con NeverBounce o ZeroBounce) antes de seguir. Muchos rebotes dañan la reputación
del dominio y hacen que todo termine en spam.

## Obligaciones legales (CAN-SPAM) — ya cubiertas por el programa
- Remitente y asunto honestos
- Dirección postal válida en cada correo
- Forma clara de darse de baja (responder STOP)
- Honrar las bajas en menos de 10 días hábiles → corre `check-replies` a diario

Nota: las respuestas llegan a luis@hinosinvestments.com. Para que `check-replies` las
vea desde Gmail, ese buzón debe reenviar a tu Gmail — o usa `"route": "godaddy"`.
