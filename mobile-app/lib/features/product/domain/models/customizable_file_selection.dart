import 'package:equatable/equatable.dart';

class CustomizableFileSelection extends Equatable {
  final String path;
  final String name;
  final int? size;

  const CustomizableFileSelection({
    required this.path,
    required this.name,
    this.size,
  });

  String get extension {
    final fileName = name.trim();
    final index = fileName.lastIndexOf('.');
    if (index < 0 || index == fileName.length - 1) return '';
    return fileName.substring(index + 1).toLowerCase();
  }

  bool isAllowedBy(List<String> allowedExtensions) {
    if (allowedExtensions.isEmpty) return true;
    final currentExtension = extension;
    if (currentExtension.isEmpty) return false;
    return allowedExtensions
        .map((value) => value.replaceFirst('.', '').toLowerCase().trim())
        .contains(currentExtension);
  }

  @override
  List<Object?> get props => [path, name, size];
}
