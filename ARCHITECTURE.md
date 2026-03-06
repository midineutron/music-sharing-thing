# Music Platform — Architecture Design

## Vision

A music-native platform combining:
- **Live rooms** (Twitch/radio): tune in to a DJ's session in real time, chat about what's playing
- **Inbox** (email-style): async music delivery organized by source type, listened on your schedule

Priority constraint: **P2P / distributed / self-hosted first**. Cloud only where unavoidable and at minimal cost.

---

## Core Concepts

### Identity: No Fixed Roles
Anyone is a listener, DJ, and artist — context-dependent. A user hosting a room is acting as a DJ. A user sharing new music to followers is acting as an artist. A **label** is a group of users publishing together under a shared identity. No account type gating — activity defines the role.

### Rooms (Live)
A room is a live session. The creator is the **host**. What role participants play depends on the **room mode**. A chat panel sits alongside the player, with messages optionally scoped to the currently-playing song — scroll back later and see what people said about a specific track.

- Rooms can be **public** (discoverable) or **private** (invite-link only)
- Room ends when the host disconnects — like a live stream ending
- DJ Drops: when a host plays or explicitly pushes a song, it can land in followers' inboxes (auto on play, or manual push — both supported)

#### Room Modes

| Mode | Playback control | Queue control |
|---|---|---|
| **Free-for-all** | Anyone | Anyone — add, reorder, delete freely |
| **DJ** | Host only | Host only — listeners are spectators with sync |
| **Request** | Host only | Anyone submits a song; host approves or rejects each one |
| **Vote** | Host (or auto) | Anyone submits; queue ordered by vote count |

Host selects mode on room creation and can change it while live.

In **Request** mode, listeners attach a full song (YouTube, Bandcamp, Arweave TX, or Nina release) to their request. Host sees the full song details and accepts (moves to queue) or rejects with an optional message.

In **Vote** mode, all members see the pending pool and cast votes. Queue is ordered by vote count. Whether the top-voted song auto-plays when the current song ends is **configurable per room** — host sets it at creation and can toggle mid-session.

### Inbox (Async)
A global, persistent, categorized inbox that lives outside any individual room. Functions like email: items arrive, you listen on your schedule.

| Inbox | Source | Analogy |
|---|---|---|
| **Direct** | Person-to-person sends | SMS / DM |
| **DJ Drops** | DJs you follow playing or pushing music | Newsletter subscription |
| **Releases** | Artists / labels pushing new music | Artist mailing list |
| **Events** | Room invitations, upcoming shows | Calendar invite |

Each inbox is independently sortable (unread first, by sender, by date, by tag).

### Groups / Labels
A label is a named group of users — anyone can create one and invite others. Followers of a group receive drops and releases from any member publishing under the group. Analogous to a Nina Protocol Hub.

---

## Architecture: P2P First

### Philosophy
- **No required central server** for the core music experience
- **Self-hostable components** for operators who want control
- **Local-first**: user data lives in the browser (IndexedDB), synced via P2P when connected
- Cloud/relay only for discovery and signaling — tiny compute, fully self-hostable

---

## Technology Stack

### Identity: Cryptographic Keypairs
- Each user gets an **Ed25519 keypair** generated and stored locally (IndexedDB / WebCrypto API)
- **Public key = user identity** — shareable, like a public wallet address
- **Private key = held client-side only** — never leaves the browser
- Compatible with [Nostr](https://nostr.com/) identity (NIP-07) — users can optionally use a browser extension (Alby, nos2x) for key management
- Display name + profile metadata signed with private key and distributed via Nostr
- No email/password required; no server stores credentials
- **Key backup**: export-only; users are prompted to back up on first use (no server-side storage)
- **Handles**: display-only nicknames, not globally unique — identity is the public key

### Real-Time Rooms: WebRTC
- Room sync moves from Firebase to **WebRTC data channels** (peer-to-peer)
- Host broadcasts playback state (current song, position, playing/paused) to all listeners
- Chat messages broadcast via the same data channels
- For small rooms (< ~20 peers): **mesh topology**
- For larger rooms: **SFU** (Selective Forwarding Unit) — self-hosted with [Mediasoup](https://mediasoup.org/) or [Janus](https://janus.conf.meetecho.com/)

#### Signaling Server (minimal, the only required server component)
WebRTC requires a brief signaling exchange (SDP offers/answers) before peers connect directly. Options:
1. **Self-hosted PeerJS Server** (`peerjs-server` npm package) — free tier on Railway/Render handles thousands of rooms
2. **Public PeerJS cloud** — free, no self-hosting needed during prototyping
3. **Nostr relay as signaling** — use any Nostr relay to exchange WebRTC signaling messages; no dedicated server at all

Once peers connect via WebRTC, the signaling server is no longer involved.

#### STUN/TURN
- **STUN**: Google's public STUN servers (free, handles most NAT traversal)
- **TURN**: Only needed for symmetric NAT (~15% of connections). Self-host [Coturn](https://github.com/coturn/coturn) on a cheap VPS, or defer

### Inbox: Nostr Protocol
[Nostr](https://nostr.com/) events are signed JSON messages broadcast to relays and filtered by subscribers. Relays are lightweight — many public ones are free; self-hosting costs ~$5/mo.

| Inbox | Delivery mechanism |
|---|---|
| Direct | Nostr NIP-17 sealed DMs (encrypted, P2P) |
| DJ Drops | Custom kind (`kind: 31337`) published by DJ when playing or pushing a song |
| Releases | Nina Protocol on-chain events (watch Solana pubkey); or Nostr for non-Nina artists |
| Events | Custom Nostr kind for room invitations |

- **DJ Drops**: DJ plays/pushes a song → signed Nostr event → arrives in followers' inbox
- **Releases**: Poll Nina Protocol API for new releases by followed artists; Nostr for artists not on Nina
- **Nina Hubs**: Following a Hub = following all releases published to it = Label/Group subscription
- **Local-first**: all received events stored in IndexedDB; relay and Nina API are delivery mechanisms only
- **Nostr scope**: used internally for inbox delivery, not claimed as a full Nostr client

### Discovery
- Public rooms published as Nostr events by hosts
- Clients subscribe to room announcements filtered by genre tag
- DJ and artist profiles discoverable by Nostr npub or handle search

---

## Data Model

### User Profile
```
{
  pubkey: string           // Ed25519 public key (hex) — the identity
  npub: string             // bech32 encoded (npub1...)
  displayName: string
  handle: string           // display-only nickname, not globally unique
  avatarUrl: string
  bio: string
  // Stored locally in IndexedDB; published as Nostr kind:0 event
}
```

### Group / Label
```
{
  groupId: string
  name: string
  handle: string           // @labelname (display only)
  ownerPubkey: string
  memberPubkeys: string[]
  avatarUrl: string
  bio: string
}
```

### Room
```
{
  roomId: string           // random ID, used for signaling + share URL
  hostPubkey: string
  name: string
  isPublic: boolean
  genre: string[]
  mode: 'freeforall' | 'dj' | 'request' | 'vote'
  voteAutoPlay: boolean    // vote mode only: auto-play top-voted song
  // Ephemeral — exists only while connected peers are present
}
```

### Room Sync Message (WebRTC data channel)
```
{
  type: 'sync' | 'chat' | 'queue_update' | 'request' | 'vote'

  // sync
  song: Song
  playing: boolean
  positionAtStart: number
  sentAt: number           // local timestamp for drift correction

  // chat
  text: string
  songRef?: string         // optional: which song this message is about

  // queue_update
  queue: Song[]

  // request (request mode)
  song: Song
  fromPubkey: string
  message?: string

  // vote
  songId: string
  fromPubkey: string
}
```

### Song
```
{
  title: string
  artist: string
  thumbnail: string
  source: 'youtube' | 'bandcamp' | 'upload_ephemeral' | 'arweave' | 'nina'
  addedBy: string          // pubkey
  addedAt: number          // unix timestamp

  // source-specific
  youtubeVideoId?: string
  bandcampUrl?: string
  ephemeralUrl?: string    // TTL-limited hosted URL
  arweaveTxId?: string     // permanent archive TX ID
  arweaveAudioUrl?: string // https://arweave.net/{txId}
  ninaReleasePublicKey?: string
  ninaHubHandle?: string
}
```

### Music Sources

| Source | Description | Storage |
|---|---|---|
| **YouTube** | Video ID, stream via IFrame API | YouTube CDN |
| **Bandcamp** | Track URL, embed player | Bandcamp |
| **Self-upload (ephemeral, in-room)** | Uploader's browser streams directly to peers via WebRTC while present | Browser only — zero cost |
| **Self-upload (ephemeral, TTL)** | File hosted with expiry for inbox delivery and async replay | Self-hosted MinIO or Cloudflare R2 free tier |
| **Self-upload → Arweave** | Opt-in permanent archival; one-time fee via Arweave wallet; payment UX TBD | Arweave permaweb |
| **Nina Protocol** | Published release; audio on Arweave, metadata/ownership on Solana | Arweave + Solana |

Nina Protocol:
- JS SDK: [`@nina-protocol/js-sdk`](https://sdk.docs.ninaprotocol.com/)
- API: [api.docs.ninaprotocol.com](http://api.docs.ninaprotocol.com/)
- A Nina **Hub** maps directly to the platform's Group/Label concept

### Nostr Inbox Event (DJ Drop / Release)
```
{
  kind: 31337
  pubkey: string           // sender's pubkey
  created_at: number       // unix timestamp
  tags: [
    ['title', 'Song Title'],
    ['artist', 'Artist Name'],
    ['source', 'youtube' | 'bandcamp' | 'arweave' | 'nina'],
    ['url', '...'],
    ['thumbnail', '...'],
    ['type', 'dj_drop' | 'release' | 'event'],
    ['genre', 'techno'],
  ]
  content: string          // optional note
  sig: string              // Ed25519 signature
}
```

### Local Inbox (IndexedDB)
```
inboxes/{category}/{eventId}
  ├── event: NostrEvent
  ├── from: string         // pubkey
  ├── fromDisplayName: string
  ├── receivedAt: number
  ├── listened: boolean
  └── category: 'direct' | 'dj_drops' | 'releases' | 'events'
```

---

## Component Architecture

```
┌──────────────────────────────────────────────────┐
│                    Browser / App                  │
│                                                   │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐  │
│  │  Room UI │   │ Inbox UI │   │ Profile / DM │  │
│  └────┬─────┘   └────┬─────┘   └──────┬───────┘  │
│       │              │                │           │
│  ┌────▼──────────────▼────────────────▼────────┐  │
│  │              App State (stores)              │  │
│  └────┬──────────────┬──────────────────────────┘  │
│       │              │                            │
│  ┌────▼────┐   ┌──────▼──────┐                   │
│  │  WebRTC │   │  IndexedDB  │                   │
│  │ Manager │   │ (local-first│                   │
│  └────┬────┘   │  inbox +    │                   │
│       │        │  identity)  │                   │
│       │        └──────┬──────┘                   │
└───────┼───────────────┼──────────────────────────┘
        │               │
   ┌────▼────┐    ┌──────▼──────────┐    ┌──────────────┐
   │Signaling│    │  Nostr Relays   │    │ Nina Protocol│
   │ Server  │    │ (public/self-   │    │  API + chain │
   │(minimal,│    │  hosted)        │    └──────────────┘
   │self-host│    └─────────────────┘
   │optional)│
   └─────────┘
```

---

## Platform Strategy: Browser → PWA → Native

```
1. Browser web app        ← entry point; zero friction, no install required
         ↓
2. PWA (installable)      ← "download the app" from the browser itself
         ↓
3. App Store (Capacitor)  ← native shell around the web app; iOS + Android distribution
         ↓
4. True native (deferred) ← only if deep OS audio / CarPlay / platform APIs require it
```

### Stage 1 — Browser Web App
All feature development happens here first. Runs in any browser, no install.

### Stage 2 — PWA
Adding a Web App Manifest + Service Worker makes the app installable from the browser — this is the "download the app" moment without an App Store.

Relevant PWA capabilities:
- **Install prompt**: native-feeling install, no App Store
- **Offline**: cached assets load without network; inbox readable offline
- **Background audio**: service worker keeps playback alive (the prototype already has an iOS keep-alive workaround; PWA service worker replaces this cleanly)
- **Push notifications**: new inbox items, room invites (Web Push API)
- **Media session API**: lock screen controls (already implemented in prototype)

### Stage 3 — App Store via Capacitor + Tauri
[Capacitor](https://capacitorjs.com/) wraps the web app in a native iOS/Android shell. Minimal code change — the same web app gets deployed into a webview with native API access. Enables App Store and Play Store distribution.

[Tauri](https://tauri.app/) does the same for desktop (macOS, Windows, Linux) — Rust-based, much smaller binaries than Electron, uses the system webview.

### Stage 4 — True Native (deferred)
Only warranted if needed:
- CarPlay / Android Auto
- Siri shortcuts
- Deep background audio on iOS (WebRTC in a webview has known limitations)
- Apple Music / Spotify library integration

If reached: **React Native + Expo** is the pragmatic path. The core logic modules (WebRTC, Nostr, Nina, IndexedDB) live in plain TypeScript, independent of the UI framework, and port cleanly.

---

## Tech Stack (Web App Rewrite)

The current single-file prototype needs to become a proper project to support PWA features, testing, and eventual Capacitor/Tauri wrapping.

| Layer | Choice | Reason |
|---|---|---|
| Framework | **SvelteKit** | Compiles to vanilla JS (no runtime); minimal bundle = fast startup on mobile; reactive stores map cleanly to existing state object; excellent PWA tooling |
| Build | Vite (included) | Fast HMR, native ESM, good plugin ecosystem |
| PWA | `vite-plugin-pwa` | Service worker + manifest, Workbox caching |
| Styling | CSS custom properties + scoped Svelte styles | No dependency; consistent across web and Capacitor |
| State | Svelte stores + IndexedDB | Reactive, local-first; no external state library |
| WebRTC | PeerJS or raw `RTCPeerConnection` | P2P room sync and data channels |
| Nostr | `nostr-tools` | Lightweight, browser-native |
| Nina | `@nina-protocol/js-sdk` | Release and hub subscriptions |
| Crypto | WebCrypto API (browser built-in) | Keypair generation and signing; no library needed |
| Mobile (Stage 3) | Capacitor | Wraps SvelteKit build for iOS + Android |
| Desktop (Stage 3) | Tauri | Wraps SvelteKit build for macOS + Windows + Linux |

### Project Structure
```
music-sharing-thing/
├── src/
│   ├── lib/
│   │   ├── core/                 // framework-agnostic TS modules
│   │   │   ├── identity.ts       // keypair generation, signing, verification
│   │   │   ├── room.ts           // WebRTC room management
│   │   │   ├── inbox.ts          // IndexedDB inbox read/write
│   │   │   ├── nostr.ts          // Nostr event pub/sub
│   │   │   └── nina.ts           // Nina Protocol SDK wrapper
│   │   └── components/           // Svelte UI components
│   │       ├── Room/
│   │       ├── Inbox/
│   │       ├── Player/
│   │       └── Search/
│   ├── routes/                   // SvelteKit pages
│   └── stores/                   // Svelte reactive stores
├── static/
│   └── manifest.webmanifest
├── capacitor.config.ts           // Stage 3
└── src-tauri/                    // Stage 3
```

---

## Migration Path from Current Prototype

### Phase 1 — Rewrite Foundation + Identity
1. Scaffold SvelteKit project; migrate HTML/CSS/JS from single file into components
2. Replace Firebase anonymous auth with local Ed25519 keypair (WebCrypto API)
3. Store user profile in IndexedDB
4. Add PWA manifest + service worker (background audio, installable)
5. Port existing YouTube and Bandcamp playback into the Player component

### Phase 2 — Rooms via WebRTC
1. Replace Firebase Realtime DB sync with WebRTC data channels (PeerJS)
2. Port existing sync logic; change transport only — logic stays the same
3. Add text chat to rooms (same WebRTC data channel)
4. Implement room modes (free-for-all first; DJ, request, vote follow)

**Cost change**: Firebase Realtime DB removed → signaling server only (free tier or self-hosted)

### Phase 3 — Global Inbox
1. Move personal queue from Firebase room-scoped → global IndexedDB inbox
2. Add inbox categories (Direct, DJ Drops, Releases, Events)
3. Add `nostr-tools`; DJ accounts publish Drop events to Nostr relays
4. Followers subscribe and receive drops into their local inbox

**Cost change**: Firebase Auth removed → $0; Nostr relays free or ~$5/mo self-hosted

### Phase 4 — Music Sources + Nina
1. Add Nina Protocol SDK; enable browsing and playing Nina releases
2. Add ephemeral self-upload (in-room WebRTC streaming)
3. Add short-term hosted upload (TTL storage for inbox delivery)
4. Add Arweave archival option (payment UX deferred)

### Phase 5 — Discovery
1. Hosts publish public room announcements as Nostr events
2. Browse public rooms by genre, friends-in-room
3. DJ / artist profiles discoverable by npub or handle

### Phase 6 — App Store Distribution
1. Add Capacitor; wrap SvelteKit build for iOS + Android
2. Add Tauri for desktop
3. Submit to App Store / Play Store

---

## Cost Model at Scale

| Component | Current | Target |
|---|---|---|
| Auth | Firebase Auth (free tier) | None — local keypairs |
| Real-time sync | Firebase Realtime DB | WebRTC P2P — $0 |
| Inbox delivery | Firebase (room-scoped) | Nostr relays — free public or ~$5/mo self-hosted |
| Signaling | None (Firebase handled it) | PeerJS Server — free tier or self-hosted |
| Storage | Firebase | IndexedDB (browser) — $0 |
| Ephemeral uploads | None | Cloudflare R2 free tier or self-hosted MinIO |
| YouTube API | Free quota | Unchanged |
| **Total** | **Firebase free tier** | **$0–$5/mo** |
