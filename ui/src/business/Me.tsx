import { ComingSoon } from "../components/ComingSoon";

export function Me(): JSX.Element {
  return (
    <ComingSoon
      title="My Activity"
      icon="👤"
      description="Your recent approvals, rejections, and overrides"
    />
  );
}
