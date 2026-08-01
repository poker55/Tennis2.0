# Tennis Score Phase 1

Tiny WebSocket prototype to test Azure hosting and real-time score updates between two phones.

## Local Run

```sh
npm install
npm start
```

Open `http://localhost:8080` on two devices. For phones on the same Wi-Fi, use your computer's LAN IP, for example `http://192.168.1.50:8080`.

## Docker Run

```sh
docker build -t tennis-score-phase1 .
docker run --rm -p 8080:8080 tennis-score-phase1
```

Open `http://localhost:8080`.

## Local Kubernetes Run

Use a local cluster such as Docker Desktop Kubernetes, minikube, or kind.

For Docker Desktop: enable **Settings -> Kubernetes -> Enable Kubernetes**, then run:

```sh
docker build -t tennis-score-phase1:latest .
kubectl config use-context docker-desktop
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/tennis-score.yaml
kubectl get pods -l app=tennis-score
kubectl get pods -l app=redis
kubectl get svc tennis-score
```

The service is a NodePort on `30080`. On Docker Desktop, local access usually works at:

```text
http://localhost:30080
```

For phone testing on the same Wi-Fi, port-forward from the Kubernetes Service to your Mac:

```sh
kubectl port-forward --address 0.0.0.0 svc/tennis-score 8080:8080
```

Then open this on the phones:

```text
http://<your-computer-lan-ip>:8080
```

Find your Mac's Wi-Fi IP with:

```sh
ipconfig getifaddr en0
```

This runs three Node.js pods plus one Redis pod. Redis stores the shared score and carries score-change events between pods, so users can connect to different Node.js pods and still see the same score.

For minikube after rebuilding the image:

```sh
minikube image load tennis-score-phase1:latest
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/tennis-score.yaml
```

## Azure Deployment

Azure Container Apps HTTP ingress supports WebSocket. The shortest deployment path is:

```sh
az login
az containerapp up \
  -n tennis-score \
  -g tennis-score-rg \
  -l germanywestcentral \
  --source . \
  --ingress external \
  --target-port 8080
```

After deployment, open the Container App URL on both phones. The page uses `wss://` automatically when served over HTTPS.

Docs:
- https://learn.microsoft.com/azure/container-apps/ingress-overview
- https://learn.microsoft.com/cli/azure/containerapp#az-containerapp-up

## Phase 2: One-Camera Ball Detection Algorithm

Phase 2 is focused on building and testing a one-camera algorithm for tennis-ball bounce and in/out detection from a fixed camera. The current prototype lives in `tennis_video_test.py` and uses classical OpenCV only. It does not use ML models, YOLO, DINO, cloud APIs, or image upload.

Run it with:

```sh
python3 tennis_video_test.py \
  --video archive/video1.mp4 \
  --calibration tested/test_01.08.2026/test1/calibration.json \
  --out tested/test_01.08.2026/test1/debug.mp4 \
  --csv tested/test_01.08.2026/test1/measurements.csv
```

If the calibration JSON does not exist, the script opens a calibration window. Click the 4 corners of the tested singles-side court region, then press Enter or Space. The manually selected singles-side polygon is treated as the authoritative in/out region, with the inner singles sideline preferred over the outer doubles sideline.

The script writes:

- A debug video with overlays
- A per-frame CSV log
- A calibration JSON file
- A printed event summary

Current detection flow:

- Use MOG2 background subtraction to find moving regions
- Enable OpenCV shadow detection
- Keep moving blobs that also match a yellow/green tennis-ball HSV range
- Filter candidates by area, bounding-box size, circularity, brightness, and distance from the previous ball position
- Use a Kalman filter to compare predicted and detected ball positions
- Combine abrupt state-change and Kalman residual scores to propose bounce candidates
- Map bounce points through the calibration homography and classify them as inside, outside, or uncertain

Current test result:

The first test version is not yet satisfying. Player shadows, racket motion, body motion, and other noisy moving regions can still be mistaken for the ball. Background removal alone is not reliable enough on the current test video, and the ball-identification algorithm needs improvement before bounce and in/out decisions can be trusted.

Phase 2 to-do list:

- Decide whether the algorithm should try to identify the ball continuously during the whole rally, or only search more aggressively around likely bounce/hit windows.
- Improve ball identification so moving shadows, player motion, racket motion, and scoreboard/video artifacts are rejected more reliably.
- Export intermediate debug images for the raw frame, foreground mask, shadow mask, color mask, combined candidate mask, and contour candidates.
- Add accepted/rejected contour overlays with rejection reasons, so detector failures can be diagnosed visually.
- Investigate why OpenCV shadow suppression still allows some shadows to become valid moving-object candidates.
- Tune the tennis-ball HSV range against real frames instead of guessing thresholds.
- Consider changing debug overlay colors so court annotations do not visually conflict with tennis-ball color detection.
- Improve tracking continuity with Kalman prediction, speed constraints, and candidate selection near the predicted ball path.
- Revisit bounce detection only after ball tracking is stable enough across several exchanges.

