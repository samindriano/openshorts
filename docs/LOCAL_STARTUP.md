# Local startup

From the `D:\Projects` folder, the normal shortcut is:

```powershell
cd Clip
web
```

The PowerShell `web` shortcut starts the GPU Compose backend, frontend, and
renderer, waits for the backend health endpoint, prints the service status,
and opens `http://localhost:5175`.

It does not rebuild or restart containers that are already running. To rebuild
the GPU backend intentionally:

```powershell
web -BuildBackend
```

To start without opening a browser:

```powershell
web -NoBrowser
```
