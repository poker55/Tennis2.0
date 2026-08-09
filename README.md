# Tennis Score

Tiny WebSocket prototype to test Azure hosting and real-time score updates between two phones.

The one-camera computer-vision prototype has been split into a sibling project:

```text
../TennisVisionOneCamera
```

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

## Scope

This project now contains only the low-data multi-device scoring app:

- Browser scoring UI in `public/index.html`
- WebSocket score and rally-event server in `server.js`
- Optional Redis-backed shared state across multiple pods
- Docker, Kubernetes, and Azure Container Apps deployment path
