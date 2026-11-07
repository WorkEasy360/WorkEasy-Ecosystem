/**
 * The Super Admin design system (2026-11-07): every console page builds from
 * these, so buttons, tabs, tables, badges, filters, KPI cards and empty states
 * look and behave the same everywhere. See docs/admin-ui/design-system.md.
 */
export { Button, ButtonLink, IconButton, CopyButton, buttonClass } from "./Button";
export type { ButtonVariant, ButtonSize } from "./Button";
export { Menu, RowMenu, usePopover } from "./Menu";
export type { MenuItem } from "./Menu";
export { Select } from "./Select";
export type { SelectOption } from "./Select";
export { FilterBar, SearchInput, StatusChips, useDebounced } from "./FilterBar";
export type { Chip } from "./FilterBar";
export { DataTable } from "./DataTable";
export type { Column } from "./DataTable";
export {
  Page,
  PageHeader,
  usePageTitle,
  LinkTabs,
  Tabs,
  Card,
  SectionHeader,
  DescriptionList,
  Alert,
  EmptyState,
  Skeleton,
  LoadingBlock,
  Avatar,
  initialsOf,
  Trunc,
  PersonCell,
  IdCell,
  Stack,
  UrlCell,
  KeyText,
  kit,
  ADMIN_TITLE_SUFFIX,
} from "./Layout";
export type { Crumb, LinkTab, StateTab, DescriptionItem, Tone } from "./Layout";
export { Badge, StatusBadge, BoolBadge, toneOf, statusLabel } from "./Badge";
export { StatCard, StatGrid, Delta } from "./Stat";
export type { StatTone } from "./Stat";
export { Tooltip, InfoTip } from "./Tooltip";
export { ConfirmDialog, useConfirm, ToastProvider, useToast } from "./Confirm";
export type { ConfirmOptions } from "./Confirm";
export * from "./format";
