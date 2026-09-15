# Nautobot Overlays

A Nautobot app for network segmentation and multi-tenancy through overlays.

## Features

- **Overlay** - Tenant network segments scoped to locations, with support for multiple isolation types
- **VXLAN** - L2 and L3 VNI management for VXLAN/EVPN overlays
- **InfiniBand PKey** - IB partition key tracking for UFM integration with GUID-based interface membership
- **InfiniBand MKey** - IB management key tracking for subnet manager integration
- **Overlay Assignments** - Associate devices, interfaces, and racks with overlays

## Screenshots

![Overlays List](images/overlays-list.png)

![Overlay Detail](images/overlay-detail.png)

![Overlay Assignments](images/overlay-assignments.png)

![VXLANs](images/vxlans.png)

![InfiniBand PKeys](images/ib-pkeys.png)

## Documentation

| Section | Description |
|---------|-------------|
| [User Guide](user/index.md) | How to use the app |
| [Installation](admin/install.md) | Setup instructions |
| [Compatibility](admin/compatibility.md) | Version matrix |
| [Release Notes](admin/release_notes/index.md) | Changelog |
| [Contributing](dev/contributing.md) | Development guide |
| [API Reference](dev/code_reference/index.md) | REST/GraphQL API |
