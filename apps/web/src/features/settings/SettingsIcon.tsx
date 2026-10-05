import { BrandMark } from "../../components/BrandMark";
import {
  ProductIcon,
  type ProductIconName,
} from "../../components/ProductIcon";

export type SettingsIconName =
  | "brand"
  | "pet"
  | "companion"
  | "voice"
  | "models"
  | "channels"
  | "data"
  | "memory"
  | "skills"
  | "plugin";

export function SettingsIcon({ name }: { name: SettingsIconName }) {
  if (name === "brand") return <BrandMark />;
  return <ProductIcon name={iconNames[name]} />;
}

const iconNames: Record<Exclude<SettingsIconName, "brand">, ProductIconName> = {
  pet: "pet",
  companion: "companion",
  voice: "voice",
  models: "models",
  channels: "channel",
  data: "data",
  memory: "memory",
  skills: "skills",
  plugin: "plugin",
};
