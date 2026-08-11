import 'api_constants.dart';

/// UXCam App Key.
/// Get this from your UXCam dashboard (Settings > App Keys).
/// Replace before release — analytics is disabled while this is a placeholder.
const String uxcamAppKey = 'kurtm1v5u25d788-us';

/// Whether [uxcamAppKey] is still the placeholder value.
bool get isUxcamAppKeyConfigured =>
    uxcamAppKey.isNotEmpty && uxcamAppKey != 'YOUR_UXCAM_APP_KEY_HERE';

/// True when [bagistoEndpoint] points at a local/dev backend
/// (the Android emulator host alias, localhost, or a LAN/private IP).
///
/// Not currently used to gate UXCam init — logging is allowed in every
/// environment for now — but kept available for when dev sessions should
/// be excluded from analytics again.
bool get isLocalDevBackend {
  final endpoint = bagistoEndpoint.toLowerCase();
  return endpoint.contains('10.0.2.2') ||
      endpoint.contains('localhost') ||
      endpoint.contains('127.0.0.1');
}
