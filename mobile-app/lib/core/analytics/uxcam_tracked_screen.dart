import 'package:flutter/material.dart';
import '../navigation/route_observer.dart';
import 'uxcam_service.dart';

/// Wraps a pushed page so UXCam knows what screen is visible.
///
/// Tags [screenName] when the page first appears and again whenever the
/// user navigates back to it (via the shared [appRouteObserver]). When
/// [sensitive] is true, the screen is also occluded from session
/// recordings for as long as it's on top — use this for any screen with
/// sensitive input (login, checkout, saved addresses, account details).
///
/// Wrap the page's root widget with it, e.g.:
/// ```dart
/// return UxcamTrackedScreen(
///   screenName: 'Login',
///   sensitive: true,
///   child: Scaffold(...),
/// );
/// ```
class UxcamTrackedScreen extends StatefulWidget {
  final String screenName;
  final bool sensitive;
  final Widget child;

  const UxcamTrackedScreen({
    super.key,
    required this.screenName,
    required this.child,
    this.sensitive = false,
  });

  @override
  State<UxcamTrackedScreen> createState() => _UxcamTrackedScreenState();
}

class _UxcamTrackedScreenState extends State<UxcamTrackedScreen>
    with RouteAware {
  PageRoute<dynamic>? _route;

  @override
  void initState() {
    super.initState();
    if (widget.sensitive) UxcamService.setSensitiveScreen(true);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final route = ModalRoute.of(context);
    if (route is PageRoute<dynamic> && route != _route) {
      if (_route != null) {
        appRouteObserver.unsubscribe(this);
      }
      _route = route;
      appRouteObserver.subscribe(this, route);
    }
    _tagScreen();
  }

  @override
  void dispose() {
    if (_route != null) {
      appRouteObserver.unsubscribe(this);
    }
    if (widget.sensitive) UxcamService.setSensitiveScreen(false);
    super.dispose();
  }

  @override
  void didPopNext() => _tagScreen();

  void _tagScreen() => UxcamService.tagScreen(widget.screenName);

  @override
  Widget build(BuildContext context) => widget.child;
}
