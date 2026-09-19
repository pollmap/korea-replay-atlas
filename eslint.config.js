import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
export default tseslint.config(
  {ignores: ['dist/**', 'node_modules/**', '.wrangler/**', '.venv/**', '.local/**', 'public/data/**']},
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {files: ['**/*.{ts,tsx,js}'], languageOptions: {globals: {...globals.browser, ...globals.node}}},
  {files: ['scripts/**/*.mjs'], languageOptions: {globals: globals.node}},
  {files: ['src/**/*.tsx'], plugins: {'react-hooks': reactHooks}, rules: {
    'react-hooks/rules-of-hooks': 'error', 'react-hooks/exhaustive-deps': 'warn',
  }},
);
