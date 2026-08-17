import '../constants/api_constants.dart';

/// Rewrites the host/port/scheme of an image URL returned by the Bagisto
/// API to match [bagistoEndpoint]'s origin.
///
/// The backend bakes its own `APP_URL` env value into every absolute image
/// URL it returns (e.g. `http://10.0.2.2:8080/storage/...`). That host is
/// only reachable from the emulator on the machine running the backend, so
/// when the app is built against a different origin (e.g. the ngrok remote
/// endpoint), the API host and the image host diverge and images fail to
/// load. Since the image path itself is always valid relative to whichever
/// backend served it, swapping in the app's own configured origin fixes it
/// for every build flavor without touching the backend.
String? resolveImageUrl(String? url) {
  if (url == null || url.isEmpty) return url;

  final apiOrigin = Uri.parse(bagistoEndpoint).origin;
  final base = Uri.parse(apiOrigin);

  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    final cleanPath = url.startsWith('/') ? url.substring(1) : url;
    return '$apiOrigin/$cleanPath';
  }

  final parsed = Uri.tryParse(url);
  if (parsed == null) return url;

  // Uri.replace(port: null) does NOT clear an existing port -- it falls
  // back to the receiver's own port, silently leaking e.g. ":8080" into a
  // host that should have no explicit port. Replacing the API origin's
  // path/query instead of the image URL's scheme/host/port sidesteps that.
  return base.replace(
    path: parsed.path,
    query: parsed.query.isEmpty ? null : parsed.query,
  ).toString();
}
