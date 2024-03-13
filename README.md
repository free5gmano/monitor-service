# monitor-service

1. build monitor-service image
   ```shell=
   docker build -t monitor-service -f monitor-service.Dockerfile .
   ```
2. run kafka server
   ```shell=
   docker compose up -d
   ```
3. run kafka server
   ```shell=
   kubectl apply -f monitor-service.yaml
   ```
