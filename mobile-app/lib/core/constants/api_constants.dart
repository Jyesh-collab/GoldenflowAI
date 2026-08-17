/// Bagisto API endpoint.
///
/// Two build flavors, chosen at build time with --dart-define (see
/// scripts/build_local.sh and scripts/build_remote.sh):
///
///   - local  (default): 'http://10.0.2.2:8080/api/graphql' — only reachable
///     from the Android emulator on this Mac (10.0.2.2 is the emulator's
///     alias for the host's localhost). Lowest latency for dev/testing here.
///
///   - remote: the ngrok URL (see bagisto-backend/start-ngrok.sh), reachable
///     from a physical device or any other network. Update
///     scripts/build_remote.sh with the new URL after every ngrok restart
///     (free ngrok URLs are random per-restart).
const String bagistoEndpoint = String.fromEnvironment(
  'BAGISTO_ENDPOINT',
  defaultValue: 'http://10.0.2.2:8080/api/graphql',
);

/// Storefront key for Bagisto API
const String storefrontKey =
    'pk_storefront_RwgpPpEpBNrKywjBExAY0QL26pUfuLtq';

/// Default channel code used by request headers.
const String channelCode = 'default';

/// Default Bagisto channel ID used during app bootstrap.
const int channelId = 1;

/// Company name
const String companyName = 'Bagisto Demo';
