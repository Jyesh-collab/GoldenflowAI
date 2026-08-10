import 'dart:developer';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Device token management service
/// Handles FCM device token storage and retrieval
class DeviceTokenService {
  static const String _deviceTokenKey = 'fcm_device_token';
  static const String _tokenGeneratedAtKey = 'fcm_token_generated_at';

  /// Save device token locally
  static Future<void> saveDeviceToken(String token) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_deviceTokenKey, token);
      await prefs.setString(
        _tokenGeneratedAtKey,
        DateTime.now().toIso8601String(),
      );
      log('💾 Device token saved: ${token.substring(0, 20)}...');
    } catch (e) {
      debugPrint('❌ Failed to save device token: $e');
    }
  }

  /// Retrieve stored device token
  static Future<String?> getDeviceToken() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final token = prefs.getString(_deviceTokenKey);
      if (token != null) {
        debugPrint('📦 Device token retrieved: ${token.substring(0, 20)}...');
      }
      return token;
    } catch (e) {
      debugPrint('❌ Failed to retrieve device token: $e');
      return null;
    }
  }

  /// Clear device token
  static Future<void> clearDeviceToken() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_deviceTokenKey);
      await prefs.remove(_tokenGeneratedAtKey);
      debugPrint('🗑️ Device token cleared');
    } catch (e) {
      debugPrint('❌ Failed to clear device token: $e');
    }
  }

  /// Get token generation timestamp
  static Future<DateTime?> getTokenGeneratedAt() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final dateString = prefs.getString(_tokenGeneratedAtKey);
      if (dateString != null) {
        return DateTime.parse(dateString);
      }
    } catch (e) {
      debugPrint('❌ Failed to get token generated time: $e');
    }
    return null;
  }

  /// Check if token is stale (older than 30 days)
  static Future<bool> isTokenStale() async {
    try {
      final generatedAt = await getTokenGeneratedAt();
      if (generatedAt == null) return true;

      final now = DateTime.now();
      final difference = now.difference(generatedAt).inDays;
      return difference > 30;
    } catch (e) {
      debugPrint('❌ Failed to check token staleness: $e');
      return true;
    }
  }
}
