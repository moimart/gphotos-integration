# Google Photos Rotator for Home Assistant

A custom integration that creates an **`image.next_photo`**-style entity which rotates a randomly (or sequentially) selected photo from a Google Photos selection on a configurable interval (default: 60 seconds).

Uses the new **Google Photos Picker API** with OAuth2 via `my.home-assistant.io` — the only path Google still supports after the March 31, 2025 Library API scope removal.

## Features

| Entity | Purpose |
|---|---|
| `image.<instance>_next_photo` | Currently displayed photo. Updates each rotation. |
| `number.<instance>_rotation_interval` | Rotation interval in seconds. Default 60, min 9, max 86400. Min is set so daily traffic stays under the Picker API's 10k req/day quota. |
| `select.<instance>_order` | `random` or `sequential`. |
| `button.<instance>_next_photo_now` | Immediately rotate. |
| `button.<instance>_re_pick_photos` | Start a new picker session to select a fresh photo set. |
| `sensor.<instance>_media_count` | Diagnostic count of items in the rotation set. |
| `sensor.<instance>_faces_count` | (Optional) Number of faces detected in the current photo; `faces` attribute holds normalized bbox list. `unavailable` unless face detection is enabled in options. |

Services: `gphotos_rotator.next` and `gphotos_rotator.repick`.

## Limitations (and why)

Google removed the `photoslibrary` / `photoslibrary.readonly` scopes from the Library API on **2025-03-31**. The replacement Picker API:

- Does not let an app list a user's albums.
- Does not auto-refresh the items inside an album that was picked.

This integration therefore stores a **snapshot of the photos you pick**. To pick up newly added photos in an album, press the **Re-pick photos** button (or call the `gphotos_rotator.repick` service) and re-select from the picker.

Picker sessions last roughly 24 hours; the integration auto-refreshes the underlying `baseUrl`s of your picked items as long as the session is alive. When the session expires you'll get a persistent notification asking you to re-pick.

## Install (HACS)

1. HACS → Integrations → ⋮ → Custom repositories.
2. Add `https://github.com/moimart/gphotos-integration` as an **Integration**.
3. Install **Google Photos Rotator**.
4. Restart Home Assistant.

## Install (manual)

Copy `custom_components/gphotos_rotator/` into your Home Assistant `config/custom_components/` directory and restart.

## Setup

### 1. Create a Google Cloud OAuth Client

You need to create your own OAuth client in Google Cloud — this is the same one-time setup the official Google Photos / Calendar / Tasks integrations use. **The client never leaves your account; the secret is only stored locally in your Home Assistant.**

1. Go to <https://console.cloud.google.com/projectcreate> and create a new project (any name).
2. Enable the **Photos Picker API**:
   <https://console.cloud.google.com/apis/library/photospicker.googleapis.com> → Enable.
3. Configure the OAuth consent screen at <https://console.cloud.google.com/apis/credentials/consent>:
   - **User type**: External.
   - App name, support email, developer contact — anything valid.
   - **Scopes**: add `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`.
   - **Test users**: add the Google account whose photos you want to use.
   - Save. You can leave the app in *Testing* mode indefinitely for personal use.
4. Create OAuth client credentials at <https://console.cloud.google.com/apis/credentials>:
   - Create credentials → OAuth client ID.
   - Application type: **Web application**.
   - Authorized redirect URI: `https://my.home-assistant.io/redirect/oauth`
   - Click Create. Copy the **Client ID** and **Client Secret**.

### 2. Add the integration in Home Assistant

1. Settings → Devices & services → **Add Integration** → search for **Google Photos Rotator**.
2. The first time, HA will ask for your **Client ID** and **Client Secret** (from step 4 above). They're stored in your local Application Credentials.
3. You'll be redirected through Google OAuth to authorize HA.
4. After auth completes, the config flow shows a **Pick photos** step with a Google Photos Picker link.
5. Open the link in your browser **signed in to the same Google account**, select an album (or individual photos), and confirm.
6. Return to Home Assistant and press **Submit**. If you submit before confirming on Google's side, you'll see a friendly "not ready yet" message — just press Submit again after confirming.
7. The integration creates the entities listed above.

### 3. Use it in a dashboard

```yaml
type: picture-entity
entity: image.gphotos_rotator_next_photo
```

The image automatically refreshes whenever the entity ticks.

### Adjusting interval / order

Open the integration's device page and use the **Rotation interval** number and **Order** select. Changes take effect on the next tick.

### Face detection (optional)

The integration can detect faces in each rotated photo locally (no cloud calls) and expose the bounding boxes on a sensor for pan-and-zoom dashboard cards.

**Enable**: Settings → Devices & services → Google Photos Rotator → **CONFIGURE** (the options dialog, not Reconfigure) → toggle **Enable face detection**.

- Backend: bundled **YuNet 2023mar** ONNX model (~230 KB) run via **onnxruntime** (no OpenCV dependency — works on Python 3.14 / HA 2026.5+).
- Cost: `onnxruntime` is ~15 MB wheel, ~50 MB on disk; ~40–60 MB RAM at runtime. Detection itself takes ~30–150 ms per photo on a Pi 4 / HA Yellow / Pi 5.
- The Python imports (`onnxruntime`, `numpy`, `PIL`) are **lazy** — disabled users pay zero RAM/CPU.
- Letterboxes input to the model's fixed 640×640 size; bboxes are returned normalized 0–1 against the original image dimensions. Verified to match `cv2.FaceDetectorYN` reference output at IoU ≥ 0.80 across test images.

**Sensor shape** (`sensor.<instance>_faces_count`):

```yaml
state: 3
attributes:
  faces:
    - {x: 0.21, y: 0.18, w: 0.12, h: 0.18, confidence: 0.93}
    - {x: 0.52, y: 0.22, w: 0.11, h: 0.17, confidence: 0.88}
    - {x: 0.74, y: 0.20, w: 0.10, h: 0.16, confidence: 0.81}
  image_width: 4032
  image_height: 3024
  detection_pending: false
  detection_ms: 78
  detector: yunet
  media_item_id: AABx...
```

Coordinates are **normalized 0–1** (multiply by your display width/height). `detection_pending: true` means the photo just rotated and detection is still running — the `faces` list is empty during this brief window. Wait for `detection_pending: false` before reading `faces` to drive an animation.

**Tuning** (same options dialog):
- *Minimum detection confidence* (default 0.6): filter out weak detections.
- *Max image dimension* (default 1280 px on the long edge): the integration downscales before detection for speed; coordinates stay normalized so this only affects latency and detection of very small faces.

### Re-picking photos

Two equivalent paths:

1. **Settings → Devices & services → Google Photos Rotator → Configure** (recommended) — opens a modal dialog with an "Open Google Photos Picker" link. Click it, pick your photos, return, and press Submit. This is the standard HA reconfigure flow.
2. **`button.<instance>_re_pick_photos`** — the in-dashboard button. Because HA backend integrations can't open browser windows directly, pressing this button programmatically starts the reconfigure flow and posts a persistent notification linking to the Configure dialog above. You'll need to click into Configure to complete the picker step.

The integration auto-detects when you've finished picking and swaps in the new photo set; the previous picker session is cleaned up.

## Troubleshooting

- **"Selection not detected yet"** — confirm your pick inside Google Photos first; the integration only sees the selection after you finish in the picker.
- **No image bytes / 401 errors** — your refresh token may have been revoked; remove the integration and re-add.
- **"Picker session expired"** notification — sessions live ~24h; press Re-pick to start fresh.
- **"Re-authentication required" banner in HA** — Google revokes OAuth refresh tokens for apps in *Testing* mode roughly every 7 days, and any user can manually revoke at <https://myaccount.google.com/permissions>. When this happens HA detects the 401 from Google, marks the entry as needing reauth, and shows a yellow banner on the integrations page. Click **Reconfigure** there to sign in again — your photo selection is preserved.
- **App stuck in "verification needed"** by Google — for personal use leave the consent screen in *Testing* mode and add yourself as a Test user; that bypasses verification.

## Credits & prior art

This integration was built after the March 2025 Google Photos API changes broke prior solutions. Concepts drawn from:

- [Daanoz/ha-google-photos](https://github.com/Daanoz/ha-google-photos) (archived) — original Library API approach.
- [eyalgal/album_slideshow](https://github.com/eyalgal/album_slideshow) — shared-album workaround using public web endpoints (not used here; we use the supported Picker API).
- [Official Home Assistant `google_photos` integration](https://www.home-assistant.io/integrations/google_photos/) — OAuth + application credentials pattern.

## License

MIT.
