import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:in_app_update/in_app_update.dart';
import 'package:upgrader/upgrader.dart';

/// Handles standard app update prompting across platforms.
///
/// Android prefers Google Play in-app updates and falls back to store lookup
/// when Play's update API is unavailable for the current install source/device.
/// iOS continues to use the store-based upgrader flow.
class AppUpdateGate extends StatelessWidget {
  final Widget child;

  const AppUpdateGate({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return _AndroidAppUpdateGate(child: child);
      case TargetPlatform.iOS:
        return _StoreUpgradeAlert(child: child);
      default:
        return child;
    }
  }
}

class _AndroidAppUpdateGate extends StatefulWidget {
  final Widget child;

  const _AndroidAppUpdateGate({required this.child});

  @override
  State<_AndroidAppUpdateGate> createState() => _AndroidAppUpdateGateState();
}

class _AndroidAppUpdateGateState extends State<_AndroidAppUpdateGate>
    with WidgetsBindingObserver {
  StreamSubscription<InstallStatus>? _installStatusSubscription;
  bool _useStoreFallback = false;
  bool _isCheckingForUpdate = false;
  bool _isRunningNativeUpdateFlow = false;
  bool _flexibleUpdateStarted = false;
  bool _nativeUpdateDismissedThisSession = false;
  bool _isRestartPromptVisible = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _installStatusSubscription = InAppUpdate.installUpdateListener.listen(
      _handleInstallStatusChanged,
      onError: (Object error, StackTrace stackTrace) {
        if (kDebugMode) {
          debugPrint('AppUpdateGate[android] install listener error: $error');
        }
      },
    );

    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_checkForUpdate());
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    super.didChangeAppLifecycleState(state);
    if (state == AppLifecycleState.resumed) {
      unawaited(_checkForUpdate());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _installStatusSubscription?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_useStoreFallback) {
      return _StoreUpgradeAlert(child: widget.child);
    }

    return widget.child;
  }

  Future<void> _checkForUpdate() async {
    if (!mounted ||
        _isCheckingForUpdate ||
        _isRunningNativeUpdateFlow ||
        _nativeUpdateDismissedThisSession) {
      return;
    }

    _isCheckingForUpdate = true;

    try {
      final info = await InAppUpdate.checkForUpdate();

      if (kDebugMode) {
        debugPrint('AppUpdateGate[android] update info: $info');
      }

      switch (info.updateAvailability) {
        case UpdateAvailability.developerTriggeredUpdateInProgress:
          await _runImmediateUpdate();
          break;
        case UpdateAvailability.updateAvailable:
          if (info.flexibleUpdateAllowed) {
            final result = await _runFlexibleUpdate();
            if (result == AppUpdateResult.userDeniedUpdate) {
              _nativeUpdateDismissedThisSession = true;
            } else if (result == AppUpdateResult.inAppUpdateFailed) {
              _enableStoreFallback('flexible update failed');
            }
          } else {
            _enableStoreFallback(
              'update available but Google Play did not allow flexible update',
            );
          }
          break;
        case UpdateAvailability.updateNotAvailable:
        case UpdateAvailability.unknown:
          if (kDebugMode) {
            debugPrint(
              'AppUpdateGate[android] no native update available: '
              '${info.updateAvailability}',
            );
          }
          break;
      }
    } on Exception catch (error) {
      _enableStoreFallback('native Android update check failed: $error');
    } finally {
      _isCheckingForUpdate = false;
    }
  }

  Future<AppUpdateResult> _runImmediateUpdate() async {
    _isRunningNativeUpdateFlow = true;
    try {
      final result = await InAppUpdate.performImmediateUpdate();
      if (kDebugMode) {
        debugPrint('AppUpdateGate[android] immediate update result: $result');
      }
      return result;
    } finally {
      _isRunningNativeUpdateFlow = false;
    }
  }

  Future<AppUpdateResult> _runFlexibleUpdate() async {
    if (_flexibleUpdateStarted) {
      if (kDebugMode) {
        debugPrint(
          'AppUpdateGate[android] flexible update already started in session',
        );
      }
      return AppUpdateResult.success;
    }

    _isRunningNativeUpdateFlow = true;
    try {
      final result = await InAppUpdate.startFlexibleUpdate();
      if (kDebugMode) {
        debugPrint('AppUpdateGate[android] flexible update result: $result');
      }
      if (result == AppUpdateResult.success) {
        _flexibleUpdateStarted = true;
      }
      return result;
    } finally {
      _isRunningNativeUpdateFlow = false;
    }
  }

  Future<void> _handleInstallStatusChanged(InstallStatus status) async {
    if (kDebugMode) {
      debugPrint('AppUpdateGate[android] install status: $status');
    }

    if (status == InstallStatus.downloaded) {
      await _showRestartToInstallPrompt();
      return;
    }

    if (status == InstallStatus.installed) {
      _flexibleUpdateStarted = false;
      _nativeUpdateDismissedThisSession = false;
      return;
    }

    if (status == InstallStatus.failed || status == InstallStatus.canceled) {
      _flexibleUpdateStarted = false;
    }
  }

  Future<void> _showRestartToInstallPrompt() async {
    if (!mounted || _isRestartPromptVisible) {
      return;
    }

    _isRestartPromptVisible = true;

    try {
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Update ready'),
          content: const Text(
            'A new version has been downloaded. Restart the app to finish installing it.',
          ),
          actions: [
            TextButton(
              onPressed: () {
                Navigator.of(dialogContext).pop();
              },
              child: const Text('Later'),
            ),
            FilledButton(
              onPressed: () async {
                Navigator.of(dialogContext).pop();
                await _completeFlexibleUpdate();
              },
              child: const Text('Restart'),
            ),
          ],
        ),
      );
    } finally {
      _isRestartPromptVisible = false;
    }
  }

  Future<void> _completeFlexibleUpdate() async {
    try {
      await InAppUpdate.completeFlexibleUpdate();
    } on Exception catch (error) {
      if (kDebugMode) {
        debugPrint(
          'AppUpdateGate[android] flexible update completion failed: $error',
        );
      }
      _enableStoreFallback('flexible update completion failed');
    }
  }

  void _enableStoreFallback(String reason) {
    if (kDebugMode) {
      debugPrint('AppUpdateGate[android] enabling store fallback: $reason');
    }

    if (_useStoreFallback || !mounted) {
      return;
    }

    setState(() {
      _useStoreFallback = true;
    });
  }
}

class _StoreUpgradeAlert extends StatelessWidget {
  final Widget child;

  const _StoreUpgradeAlert({required this.child});

  @override
  Widget build(BuildContext context) {
    return UpgradeAlert(
      upgrader: Upgrader(
        durationUntilAlertAgain: const Duration(days: 1),
        debugLogging: kDebugMode,
      ),
      child: child,
    );
  }
}
