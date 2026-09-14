FROM node:22-bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip

COPY package.json package-lock.json ./
RUN npm ci

COPY requirements-cad.txt ./
RUN pip3 install --break-system-packages --no-cache-dir -r requirements-cad.txt

COPY . .
RUN npm run build && mkdir -p output/generated && chmod +x scripts/start-render.sh

ENV NODE_ENV=production
ENV L_DXF_BIND_HOST=127.0.0.1
ENV L_DXF_PORT=3001
ENV L_DXF_INTERNAL_URL=http://127.0.0.1:3001

EXPOSE 10000
CMD ["./scripts/start-render.sh"]
