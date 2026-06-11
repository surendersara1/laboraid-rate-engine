"""L5 AI stack — Bedrock PII Guardrail (Spec/09 §5.6).

POC AI footprint is intentionally minimal: one PII Guardrail applied to every
Bedrock InvokeModel call (the agent's Path-C fallback + the classifier's Haiku
call). No Bedrock Knowledge Base / AgentCore Memory/Gateway (deferred, §15).

The guardrail ID is exported for the processing stack to inject into the
ExtractorAgent runtime env (``BEDROCK_GUARDRAIL_ID``).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_kms as kms
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.util.naming import name


class AiStack(Stack):
    """Bedrock Guardrails (PII)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        master_key: kms.IKey,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "PiiGuardrail",
            name=name(config.env, "l5", "guardrail", "pii"),
            blocked_input_messaging="Input contains PII; please redact before resubmitting.",
            blocked_outputs_messaging="Output would contain PII; suppressed.",
            kms_key_arn=master_key.key_arn,
            sensitive_information_policy_config=(
                bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                    # ANONYMIZE (mask), not BLOCK. The extractor reads the
                    # customer's own union rate documents, which legitimately
                    # carry fund-office phone/email in a trustee block. BLOCK
                    # rejected the whole document ("Input contains PII") so
                    # Claude extracted 0 rows from the 281 wage sheets — the
                    # PII filter was silently killing real extractions.
                    # ANONYMIZE masks any actual phone/email/SSN before the
                    # model sees it (still protected) while letting the rate
                    # tables — which are not PII — through.
                    pii_entities_config=[
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(type="EMAIL", action="ANONYMIZE"),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(type="PHONE", action="ANONYMIZE"),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="US_SOCIAL_SECURITY_NUMBER", action="ANONYMIZE"
                        ),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="CREDIT_DEBIT_CARD_NUMBER", action="ANONYMIZE"
                        ),
                    ],
                )
            ),
        )

        self.guardrail_id = self.guardrail.attr_guardrail_id

        CfnOutput(self, "GuardrailId", value=self.guardrail_id)
        CfnOutput(self, "GuardrailArn", value=self.guardrail.attr_guardrail_arn)
