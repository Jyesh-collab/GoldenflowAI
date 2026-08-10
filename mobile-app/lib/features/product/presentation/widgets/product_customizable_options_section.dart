import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:file_picker/file_picker.dart';

import '../../../../core/theme/app_theme.dart';
import '../../../category/data/models/product_model.dart';
import '../../domain/models/customizable_file_selection.dart';
import '../bloc/product_detail_bloc.dart';

class ProductCustomizableOptionsSection extends StatelessWidget {
  final ProductModel product;

  const ProductCustomizableOptionsSection({super.key, required this.product});

  @override
  Widget build(BuildContext context) {
    if (product.customizableOptions.isEmpty) {
      return const SizedBox.shrink();
    }

    return BlocBuilder<ProductDetailBloc, ProductDetailState>(
      buildWhen: (previous, current) =>
          previous.customizableOptionValues != current.customizableOptionValues,
      builder: (context, state) {
        final options = [...product.customizableOptions]
          ..sort((a, b) => (a.sortOrder ?? 0).compareTo(b.sortOrder ?? 0));

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Custom Options',
              style: TextStyle(
                fontFamily: 'Roboto',
                fontSize: 16,
                fontWeight: FontWeight.w600,
                color: _textColor(context),
              ),
            ),
            const SizedBox(height: 14),
            for (final option in options) ...[
              _buildOption(context, option),
              const SizedBox(height: 18),
            ],
          ],
        );
      },
    );
  }

  Widget _buildOption(BuildContext context, ProductCustomizableOption option) {
    switch (option.normalizedType) {
      case 'text':
        return _TextOptionField(option: option);
      case 'textarea':
        return _TextOptionField(option: option, maxLines: 4);
      case 'checkbox':
      case 'multiselect':
        return _MultiChoiceOption(option: option);
      case 'radio':
        return _SingleChoiceOption(option: option);
      case 'select':
        return _SelectOption(option: option);
      case 'date':
        return _PickerOption(option: option, mode: _PickerMode.date);
      case 'datetime':
        return _PickerOption(option: option, mode: _PickerMode.dateTime);
      case 'time':
        return _PickerOption(option: option, mode: _PickerMode.time);
      case 'file':
        return _FileOption(option: option);
      default:
        return _TextOptionField(option: option);
    }
  }

  Color _textColor(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return isDark ? AppColors.neutral100 : AppColors.neutral900;
  }
}

class _OptionLabel extends StatelessWidget {
  final ProductCustomizableOption option;
  final bool includeSinglePrice;

  const _OptionLabel({required this.option, this.includeSinglePrice = false});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final price = includeSinglePrice
        ? option.singlePriceOption?.formattedPrice?.trim()
        : null;

    return RichText(
      text: TextSpan(
        style: TextStyle(
          fontFamily: 'Roboto',
          fontSize: 14,
          fontWeight: FontWeight.w500,
          color: isDark ? AppColors.neutral100 : AppColors.neutral900,
        ),
        children: [
          TextSpan(text: option.displayLabel),
          if (option.isRequired)
            const TextSpan(
              text: ' *',
              style: TextStyle(color: AppColors.primary500),
            ),
          if (price != null && price.isNotEmpty)
            TextSpan(
              text: ' + $price',
              style: TextStyle(
                color: isDark ? AppColors.neutral300 : AppColors.neutral700,
              ),
            ),
        ],
      ),
    );
  }
}

class _TextOptionField extends StatelessWidget {
  final ProductCustomizableOption option;
  final int maxLines;

  const _TextOptionField({required this.option, this.maxLines = 1});

  @override
  Widget build(BuildContext context) {
    final value = context.select<ProductDetailBloc, String>(
      (bloc) =>
          bloc.state.customizableOptionValues[option.id]?.toString() ?? '',
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option, includeSinglePrice: true),
        const SizedBox(height: 8),
        TextFormField(
          initialValue: value,
          maxLines: maxLines,
          maxLength: option.maxCharacters,
          textInputAction: maxLines > 1
              ? TextInputAction.newline
              : TextInputAction.done,
          decoration: _inputDecoration(context),
          onChanged: (newValue) {
            context.read<ProductDetailBloc>().add(
              UpdateCustomizableOption(optionId: option.id, value: newValue),
            );
          },
        ),
      ],
    );
  }
}

class _MultiChoiceOption extends StatelessWidget {
  final ProductCustomizableOption option;

  const _MultiChoiceOption({required this.option});

  @override
  Widget build(BuildContext context) {
    final selected = context.select<ProductDetailBloc, List<String>>(
      (bloc) => _asStringList(bloc.state.customizableOptionValues[option.id]),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option),
        const SizedBox(height: 8),
        ...option.prices.map((price) {
          final priceId = price.id;
          final isSelected = selected.contains(priceId);
          return CheckboxListTile(
            key: ValueKey('custom_option_${option.id}_$priceId'),
            value: isSelected,
            dense: true,
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            title: Text(price.displayLabel, style: _optionTextStyle(context)),
            activeColor: AppColors.primary500,
            onChanged: (_) {
              final updated = List<String>.from(selected);
              if (isSelected) {
                updated.remove(priceId);
              } else {
                updated.add(priceId);
              }
              context.read<ProductDetailBloc>().add(
                UpdateCustomizableOption(optionId: option.id, value: updated),
              );
            },
          );
        }),
      ],
    );
  }
}

class _SingleChoiceOption extends StatelessWidget {
  final ProductCustomizableOption option;

  const _SingleChoiceOption({required this.option});

  @override
  Widget build(BuildContext context) {
    final selected = context.select<ProductDetailBloc, String?>(
      (bloc) => bloc.state.customizableOptionValues[option.id]?.toString(),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option),
        const SizedBox(height: 8),
        if (!option.isRequired)
          _choiceRow(
            context,
            label: 'None',
            selected: selected == null,
            onTap: () {
              context.read<ProductDetailBloc>().add(
                UpdateCustomizableOption(optionId: option.id, value: null),
              );
            },
          ),
        ...option.prices.map((price) {
          return _choiceRow(
            context,
            key: ValueKey('custom_option_${option.id}_${price.id}'),
            label: price.displayLabel,
            selected: selected == price.id,
            onTap: () {
              context.read<ProductDetailBloc>().add(
                UpdateCustomizableOption(optionId: option.id, value: price.id),
              );
            },
          );
        }),
      ],
    );
  }

  Widget _choiceRow(
    BuildContext context, {
    Key? key,
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return InkWell(
      key: key,
      borderRadius: BorderRadius.circular(8),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Icon(
              selected
                  ? Icons.radio_button_checked
                  : Icons.radio_button_unchecked,
              size: 22,
              color: selected ? AppColors.primary500 : AppColors.neutral500,
            ),
            const SizedBox(width: 12),
            Expanded(child: Text(label, style: _optionTextStyle(context))),
          ],
        ),
      ),
    );
  }
}

class _SelectOption extends StatelessWidget {
  final ProductCustomizableOption option;

  const _SelectOption({required this.option});

  @override
  Widget build(BuildContext context) {
    final selected = context.select<ProductDetailBloc, String?>(
      (bloc) => bloc.state.customizableOptionValues[option.id]?.toString(),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option),
        const SizedBox(height: 8),
        DropdownButtonFormField<String?>(
          initialValue: selected,
          isExpanded: true,
          decoration: _inputDecoration(context),
          items: [
            if (!option.isRequired)
              const DropdownMenuItem<String?>(value: null, child: Text('None')),
            ...option.prices.map(
              (price) => DropdownMenuItem<String?>(
                value: price.id,
                child: Text(price.displayLabel),
              ),
            ),
          ],
          onChanged: (value) {
            context.read<ProductDetailBloc>().add(
              UpdateCustomizableOption(optionId: option.id, value: value),
            );
          },
        ),
      ],
    );
  }
}

enum _PickerMode { date, dateTime, time }

class _PickerOption extends StatelessWidget {
  final ProductCustomizableOption option;
  final _PickerMode mode;

  const _PickerOption({required this.option, required this.mode});

  @override
  Widget build(BuildContext context) {
    final value = context.select<ProductDetailBloc, String?>(
      (bloc) => bloc.state.customizableOptionValues[option.id]?.toString(),
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option, includeSinglePrice: true),
        const SizedBox(height: 8),
        InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: () => _pickValue(context),
          child: InputDecorator(
            decoration: _inputDecoration(context),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    (value?.isNotEmpty ?? false)
                        ? value!
                        : _placeholderForMode(mode),
                    style: _optionTextStyle(context).copyWith(
                      color: (value?.isNotEmpty ?? false)
                          ? null
                          : AppColors.neutral500,
                    ),
                  ),
                ),
                const Icon(Icons.calendar_today_outlined, size: 18),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _pickValue(BuildContext context) async {
    final now = DateTime.now();
    if (mode == _PickerMode.time) {
      final time = await showTimePicker(
        context: context,
        initialTime: TimeOfDay.now(),
      );
      if (time == null || !context.mounted) return;
      _setValue(context, _formatTime(time));
      return;
    }

    final date = await showDatePicker(
      context: context,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 10),
      initialDate: now,
    );
    if (date == null || !context.mounted) return;

    if (mode == _PickerMode.date) {
      _setValue(context, _formatDate(date));
      return;
    }

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.now(),
    );
    if (time == null || !context.mounted) return;
    _setValue(context, '${_formatDate(date)} ${_formatTime(time)}');
  }

  void _setValue(BuildContext context, String value) {
    context.read<ProductDetailBloc>().add(
      UpdateCustomizableOption(optionId: option.id, value: value),
    );
  }

  String _placeholderForMode(_PickerMode mode) {
    switch (mode) {
      case _PickerMode.date:
        return 'Select date';
      case _PickerMode.dateTime:
        return 'Select date and time';
      case _PickerMode.time:
        return 'Select time';
    }
  }

  String _formatDate(DateTime date) {
    final month = date.month.toString().padLeft(2, '0');
    final day = date.day.toString().padLeft(2, '0');
    return '${date.year}-$month-$day';
  }

  String _formatTime(TimeOfDay time) {
    final hour = time.hour.toString().padLeft(2, '0');
    final minute = time.minute.toString().padLeft(2, '0');
    return '$hour:$minute';
  }
}

class _FileOption extends StatelessWidget {
  final ProductCustomizableOption option;

  const _FileOption({required this.option});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final selected = context
        .select<ProductDetailBloc, CustomizableFileSelection?>((bloc) {
          final value = bloc.state.customizableOptionValues[option.id];
          return value is CustomizableFileSelection ? value : null;
        });
    final supportedExtensions = option.supportedFileExtensionList;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _OptionLabel(option: option, includeSinglePrice: true),
        const SizedBox(height: 8),
        InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: () => _pickFile(context, supportedExtensions),
          child: InputDecorator(
            decoration: _inputDecoration(context),
            child: Row(
              children: [
                Icon(
                  selected == null
                      ? Icons.upload_file_outlined
                      : Icons.insert_drive_file_outlined,
                  size: 20,
                  color: selected == null
                      ? AppColors.neutral500
                      : AppColors.primary500,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    selected?.name ?? 'Choose file',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: _optionTextStyle(context).copyWith(
                      color: selected == null
                          ? AppColors.neutral500
                          : (isDark
                                ? AppColors.neutral100
                                : AppColors.neutral800),
                    ),
                  ),
                ),
                if (selected != null) ...[
                  const SizedBox(width: 8),
                  IconButton(
                    tooltip: 'Remove file',
                    visualDensity: VisualDensity.compact,
                    onPressed: () {
                      context.read<ProductDetailBloc>().add(
                        UpdateCustomizableOption(
                          optionId: option.id,
                          value: null,
                        ),
                      );
                    },
                    icon: const Icon(Icons.close, size: 18),
                  ),
                ],
              ],
            ),
          ),
        ),
        if (supportedExtensions.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            'Allowed: ${supportedExtensions.join(', ')}',
            style: _optionTextStyle(context).copyWith(
              fontSize: 12,
              color: isDark ? AppColors.neutral400 : AppColors.neutral500,
            ),
          ),
        ],
      ],
    );
  }

  Future<void> _pickFile(
    BuildContext context,
    List<String> supportedExtensions,
  ) async {
    final result = await FilePicker.pickFiles(
      type: supportedExtensions.isEmpty ? FileType.any : FileType.custom,
      allowedExtensions: supportedExtensions.isEmpty
          ? null
          : supportedExtensions,
      allowMultiple: false,
      withData: false,
    );

    if (result == null || result.files.isEmpty || !context.mounted) return;

    final file = result.files.single;
    final path = file.path;
    if (path == null || path.isEmpty) {
      _showSnackBar(context, 'Selected file is not available');
      return;
    }

    final selection = CustomizableFileSelection(
      path: path,
      name: file.name,
      size: file.size,
    );

    if (!selection.isAllowedBy(supportedExtensions)) {
      _showSnackBar(
        context,
        'Please select ${supportedExtensions.join(', ')} file',
      );
      return;
    }

    context.read<ProductDetailBloc>().add(
      UpdateCustomizableOption(optionId: option.id, value: selection),
    );
  }

  void _showSnackBar(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), duration: const Duration(seconds: 2)),
    );
  }
}

InputDecoration _inputDecoration(BuildContext context) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  final borderColor = isDark ? AppColors.neutral700 : AppColors.neutral200;

  return InputDecoration(
    isDense: true,
    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
    enabledBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(10),
      borderSide: BorderSide(color: borderColor),
    ),
    focusedBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(10),
      borderSide: const BorderSide(color: AppColors.primary500),
    ),
    errorBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(10),
      borderSide: const BorderSide(color: Colors.red),
    ),
    focusedErrorBorder: OutlineInputBorder(
      borderRadius: BorderRadius.circular(10),
      borderSide: const BorderSide(color: Colors.red),
    ),
  );
}

TextStyle _optionTextStyle(BuildContext context) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  return TextStyle(
    fontFamily: 'Roboto',
    fontSize: 14,
    fontWeight: FontWeight.w400,
    color: isDark ? AppColors.neutral100 : AppColors.neutral800,
  );
}

List<String> _asStringList(dynamic value) {
  if (value == null) return const [];
  if (value is List) return value.map((item) => item.toString()).toList();
  return [value.toString()];
}
