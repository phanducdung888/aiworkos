# The web surface, as a deployment rather than as a developer's terminal.
#
# Until now the only way to see this application was `npm run dev` on the host: a Vite dev server,
# a host Node version that had to be new enough (v16 fails on `node:fs/promises` in a way that
# names neither Vite nor the version), and a process nothing supervised. So "start the system" was
# `docker compose up` *and* a second thing somebody had to remember. This image is the second thing,
# removed.
#
# Two stages, because the result of a build is static files. The toolchain that produces them has no
# business in the image that serves them — it is the larger half, and every dependency in it would
# be a dependency of the running system.

FROM node:22-alpine AS build

WORKDIR /src

# The lockfile alone first, so a change to application source does not reinstall the world.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The three values the application needs before it can ask anybody to sign in (`src/auth/oidc.ts`
# refuses to start without them). They are **build** arguments and not environment variables,
# because Vite inlines them into the bundle: there is no runtime in a static file to read an
# environment from, so changing one of these means rebuilding this image. Nothing here is secret —
# a public OIDC client has no secret by design, and the redirect URI is visible in the URL bar.
#
# The defaults are the local stack's addresses, which are also what `.env.example` carries.
ARG VITE_OIDC_AUTHORITY=http://localhost:8080/realms/workos
ARG VITE_OIDC_CLIENT_ID=workos-web
ARG VITE_OIDC_REDIRECT_URI=http://localhost:5173/auth/callback
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY \
    VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID \
    VITE_OIDC_REDIRECT_URI=$VITE_OIDC_REDIRECT_URI

# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the image rather than
# shipping a bundle that breaks in a browser.
RUN npm run build


FROM nginx:1.27-alpine

# The server configuration is mounted by docker-compose.yml from ops/proxy/web.conf, the same way
# the object proxy's is: it describes the deployment's topology, and topology lives with the other
# deployment files rather than baked into an image that would have to be rebuilt to change a
# proxy_pass.
COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
