import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:equatable/equatable.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../../../core/recently_viewed/recently_viewed_products_service.dart';
import '../../../../core/notifications/fcm_service.dart';

/// State for the Settings bottom sheet.
/// Manages notification toggles, offline data toggles, and theme mode.
///
/// Figma node-id: 248-8062 (pop-over-settings-light)
class SettingsState extends Equatable {
  final bool allNotifications;
  final bool ordersNotification;
  final bool offersNotification;
  final bool trackRecentlyViewed;
  final bool showSearchTag;

  const SettingsState({
    this.allNotifications = true,
    this.ordersNotification = true,
    this.offersNotification = true,
    this.trackRecentlyViewed = true,
    this.showSearchTag = true,
  });

  SettingsState copyWith({
    bool? allNotifications,
    bool? ordersNotification,
    bool? offersNotification,
    bool? trackRecentlyViewed,
    bool? showSearchTag,
  }) {
    return SettingsState(
      allNotifications: allNotifications ?? this.allNotifications,
      ordersNotification: ordersNotification ?? this.ordersNotification,
      offersNotification: offersNotification ?? this.offersNotification,
      trackRecentlyViewed: trackRecentlyViewed ?? this.trackRecentlyViewed,
      showSearchTag: showSearchTag ?? this.showSearchTag,
    );
  }

  @override
  List<Object?> get props => [
    allNotifications,
    ordersNotification,
    offersNotification,
    trackRecentlyViewed,
    showSearchTag,
  ];
}

/// Cubit to manage Settings page state.
class SettingsCubit extends Cubit<SettingsState> {
  static const _allNotificationsKey = 'settings_all_notifications';
  static const _ordersNotificationKey = 'settings_orders_notification';
  static const _offersNotificationKey = 'settings_offers_notification';
  static const _trackRecentlyViewedKey = 'settings_track_recently_viewed';
  static const _showSearchTagKey = 'settings_show_search_tag';

  SettingsCubit() : super(const SettingsState()) {
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();

    emit(
      SettingsState(
        allNotifications: prefs.getBool(_allNotificationsKey) ?? true,
        ordersNotification: prefs.getBool(_ordersNotificationKey) ?? true,
        offersNotification: prefs.getBool(_offersNotificationKey) ?? true,
        trackRecentlyViewed:
            prefs.getBool(_trackRecentlyViewedKey) ?? true,
        showSearchTag: prefs.getBool(_showSearchTagKey) ?? true,
      ),
    );
    _applyNotifications(state.allNotifications);
  }

  Future<void> _persistState(SettingsState nextState) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_allNotificationsKey, nextState.allNotifications);
    await prefs.setBool(
      _ordersNotificationKey,
      nextState.ordersNotification,
    );
    await prefs.setBool(
      _offersNotificationKey,
      nextState.offersNotification,
    );
    await prefs.setBool(
      _trackRecentlyViewedKey,
      nextState.trackRecentlyViewed,
    );
    await prefs.setBool(_showSearchTagKey, nextState.showSearchTag);
  }

  void _emitAndPersist(SettingsState nextState) {
    emit(nextState);
    _persistState(nextState);
  }

  Future<void> _applyNotifications(bool enabled) async {
    try {
      await FCMService().setNotificationsEnabled(enabled);
    } catch (e) {
      // Keep UI state; log silently.
      // ignore: avoid_print
      print('⚠️ Failed to toggle notifications: $e');
    }
  }

  void toggleAllNotifications(bool value) {
    final nextState = state.copyWith(
      allNotifications: value,
      ordersNotification: value,
      offersNotification: value,
    );

    _emitAndPersist(nextState);
    _applyNotifications(nextState.allNotifications);
  }

  void toggleOfflineData(bool value) {
    final nextState = state.copyWith(
      trackRecentlyViewed: value,
      showSearchTag: value,
    );

    _emitAndPersist(nextState);
    if (!value) {
      RecentlyViewedProductsService.clear();
    }
  }

  void toggleOrdersNotification(bool value) {
    final nextState = state.copyWith(
      ordersNotification: value,
      allNotifications: value && state.offersNotification,
    );

    _emitAndPersist(nextState);
    _applyNotifications(nextState.allNotifications);
  }

  void toggleOffersNotification(bool value) {
    final nextState = state.copyWith(
      offersNotification: value,
      allNotifications: state.ordersNotification && value,
    );

    _emitAndPersist(nextState);
    _applyNotifications(nextState.allNotifications);
  }

  void toggleTrackRecentlyViewed(bool value) {
    final nextState = state.copyWith(trackRecentlyViewed: value);
    _emitAndPersist(nextState);
    if (!value) {
      RecentlyViewedProductsService.clear();
    }
  }

  void toggleShowSearchTag(bool value) {
    final nextState = state.copyWith(showSearchTag: value);
    _emitAndPersist(nextState);
  }
}
