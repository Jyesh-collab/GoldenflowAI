import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'currency_formatter.dart';

class CurrencyCubit extends Cubit<String?> {
  static const String currencyKey = 'app_currency_code';
  late SharedPreferences _prefs;

  CurrencyCubit() : super(null);

  Future<void> initialize(SharedPreferences prefs) async {
    _prefs = prefs;
    CurrencyFormatter.initialize(prefs);
    final savedCode = _prefs.getString(currencyKey);
    if (savedCode == null || savedCode.isEmpty) return;
    emit(savedCode);
  }

  Future<void> setCurrency(String currencyCode) async {
    emit(currencyCode);
    await CurrencyFormatter.updateSelectedCurrency(
      currencyCode,
      prefs: _prefs,
    );
  }

  Future<void> clearCurrency() async {
    emit(null);
    await CurrencyFormatter.updateSelectedCurrency(null, prefs: _prefs);
  }
}
