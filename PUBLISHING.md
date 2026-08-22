# Publishing checklist

This package is prepared as a GitHub/HACS repository template. Before the first
public push, configure the real GitHub owner once:

```bash
python3 tools/configure_repository.py cm86
```

The script updates:

- `manifest.json` `codeowners`
- `manifest.json` `documentation`
- `manifest.json` `issue_tracker`
- README repository URLs

Then run:

```bash
python3 tools/check_repository.py
python3 -m compileall -q custom_components/kress_fleet
find custom_components/kress_fleet -type d -name __pycache__ -prune -exec rm -rf {} +
find custom_components/kress_fleet -type f -name '*.pyc' -delete
```

Recommended GitHub repository settings:

- Repository name: `home-assistant-kress-fleet`
- Description: `Unofficial Home Assistant integration for Kress Fleet mowers`
- Public repository
- Issues enabled
- Suggested topics: `home-assistant`, `hacs`, `kress`, `kress-fleet`, `robot-mower`, `lawn-mower`

## First release

1. Push the repository.
2. Confirm the **HACS validation**, **hassfest** and **Repository checks** workflows are green.
3. Create/push tag `v0.3.0`.
4. The included release workflow creates a GitHub Release from that tag.
5. Add the repository to HACS as a custom repository of type **Integration** and test install/update.

## HACS default repository later

The validation workflow currently ignores the HACS `brands` check because this
repository deliberately does not redistribute the official Kress logo. Home
Assistant 2026.3+ supports local brand assets for custom integrations. If you
later create original neutral community artwork, place at least `icon.png` under
`custom_components/kress_fleet/brand/` and remove `ignore: brands` from
`.github/workflows/validate.yml`. Do not copy the official Kress/Fleet logo.

HACS default inclusion also expects a real GitHub release, repository description,
topics, issues enabled and passing HACS validation.

## Migration note

`kress_fleet` is a new Home Assistant domain. Existing private/test installations
of `landroid_fleet` must remove the old config entry/component and add the new
integration once. Do not publish an automatic `.storage` mutation script.
