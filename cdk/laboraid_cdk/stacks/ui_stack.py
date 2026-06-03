"""L1 UI hosting stack — S3 + CloudFront + OAC + BucketDeployment (Spec/09 §4 L1 §1.3).

Hosts the React SPA build (`ui/dist`) from a private S3 bucket fronted by
CloudFront with Origin Access Control. A single distribution serves both
`/admin/*` and `/business/*` (SPA fallback rewrites 403/404 to `/index.html`).

The custom domain (ACM cert in us-east-1 + Route53 A record) is wired only when
`config.has_custom_domain` — the existing hosted zone is resolved internally via
`HostedZone.from_lookup`. With no custom domain (the POC default) the SPA is
served from the CloudFront default `*.cloudfront.net` domain (audit B8 / decision
D-B8). Either way `self.app_url` is the canonical browser URL, consumed by the
security stack for the Cognito hosted-UI callback URLs so the auth flow always
lands somewhere resolvable. The Cognito hosted-UI domain itself lives in the
security stack.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cf
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deploy
from constructs import Construct

from laboraid_cdk.config import Config
from laboraid_cdk.util.naming import name


class UiStack(Stack):
    """Private S3 + CloudFront (OAC) hosting for the two-persona SPA."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env = config.env

        self.spa_bucket = s3.Bucket(
            self,
            "SpaBucket",
            bucket_name=name(env, "l1", "bucket", "spa"),
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=(RemovalPolicy.RETAIN if config.is_prod else RemovalPolicy.DESTROY),
            auto_delete_objects=not config.is_prod,
        )

        # Optional custom domain. When configured, resolve the existing hosted
        # zone for the parent domain (e.g. `laboraid.app` for `admin.laboraid.app`)
        # and provision the ACM cert. On an environment-agnostic stack (no `env=`)
        # `from_lookup` returns a dummy zone so synth stays credential-free; the
        # real lookup happens at deploy.
        hosted_zone: route53.IHostedZone | None = None
        certificate: acm.ICertificate | None = None
        domain_names: list[str] | None = None
        if config.has_custom_domain:
            assert config.domain_name is not None  # guaranteed by has_custom_domain
            parent_domain = config.domain_name.split(".", 1)[1]
            hosted_zone = route53.HostedZone.from_lookup(self, "SpaZone", domain_name=parent_domain)
            certificate = acm.Certificate(
                self,
                "SpaCert",
                domain_name=config.domain_name,
                validation=acm.CertificateValidation.from_dns(hosted_zone),
            )
            domain_names = [config.domain_name]

        oac = cf.S3OriginAccessControl(self, "SpaOac")
        self.distribution = cf.Distribution(
            self,
            "SpaDistribution",
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    self.spa_bucket, origin_access_control=oac
                ),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            price_class=cf.PriceClass.PRICE_CLASS_100,
            error_responses=[
                # SPA client-side routing: serve index.html for 403/404.
                cf.ErrorResponse(
                    http_status=403, response_http_status=200, response_page_path="/index.html"
                ),
                cf.ErrorResponse(
                    http_status=404, response_http_status=200, response_page_path="/index.html"
                ),
            ],
            certificate=certificate,
            domain_names=domain_names,
        )

        if hosted_zone is not None and config.domain_name is not None:
            route53.ARecord(
                self,
                "SpaAliasRecord",
                zone=hosted_zone,
                record_name=config.domain_name,
                target=route53.RecordTarget.from_alias(
                    route53_targets.CloudFrontTarget(self.distribution)
                ),
            )
            self.app_url = f"https://{config.domain_name}"
        else:
            # No custom domain — the SPA lives at the CloudFront default domain.
            self.app_url = f"https://{self.distribution.distribution_domain_name}"

        # Deploy the React build (cd ui && pnpm build -> ui/dist).
        s3_deploy.BucketDeployment(
            self,
            "SpaDeployment",
            sources=[s3_deploy.Source.asset("../ui/dist")],
            destination_bucket=self.spa_bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "DistributionDomain", value=self.distribution.distribution_domain_name)
        CfnOutput(self, "AppUrl", value=self.app_url)
