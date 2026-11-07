import { Icon } from "@/components/sidebar/icons";
import { buttonClass, type ButtonSize, type ButtonVariant } from "./Button";

/**
 * For a FormPopup's opening button (Modal.tsx): the kit's button look and an
 * icon instead of a typed "+". The accessible name is the text alone.
 *
 *   <FormPopup label={iconLabel("New coupon")} buttonClassName={popupButton()} … />
 */
export function iconLabel(text: string, icon = "plus") {
  return (
    <>
      <Icon name={icon} size={16} />
      <span>{text}</span>
    </>
  );
}

export function popupButton(variant: ButtonVariant = "primary", size: ButtonSize = "md") {
  return buttonClass(variant, size);
}
