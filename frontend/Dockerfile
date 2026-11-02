# =============================================================================
# Wolf DevSecOps — frontend (React/Vite) compilado y servido por Nginx.
# Etapa 1: build con Node (tsc -b && vite build). Etapa 2: Nginx sirve /dist.
# =============================================================================
FROM node:20-slim AS build
WORKDIR /app
# Copiamos el lockfile y usamos `npm ci`: build reproducible y pineado al lock
# (lo mismo que escanea Trivy en package-lock.json), no una resolución fresca.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Imagen unprivileged: corre como uid 101 (nginx), no root → cierra DS002/KSV012.
# Escucha en 8080 (puerto no privilegiado, no requiere NET_BIND_SERVICE) y solo
# escribe en /tmp, compatible con readOnlyRootFilesystem en k8s.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Explícito para el check estático DS002 (la imagen base ya corre como este uid).
USER 101
EXPOSE 8080
# DS026: verifica que Nginx sirve el SPA.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1
CMD ["nginx", "-g", "daemon off;"]
