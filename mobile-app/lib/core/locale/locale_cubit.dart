import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:shared_preferences/shared_preferences.dart';

class LocaleCubit extends Cubit<Locale?> {
  static const String localeKey = 'app_locale_code';
  late SharedPreferences _prefs;

  LocaleCubit() : super(null);

  Future<void> initialize(SharedPreferences prefs) async {
    _prefs = prefs;
    final savedCode = _prefs.getString(localeKey);
    if (savedCode == null || savedCode.isEmpty) return;
    emit(Locale(savedCode));
  }

  Future<void> setLocale(String languageCode) async {
    emit(Locale(languageCode));
    await _prefs.setString(localeKey, languageCode);
  }

  Future<void> clearLocale() async {
    emit(null);
    await _prefs.remove(localeKey);
  }
}
