# The AWS CLI plus AWS's Roles Anywhere signing helper, which turns the server's certificate into
# one-hour AWS credentials. The helper is pinned and checked against the SHA-256 sums AWS publishes
# in the Roles Anywhere user guide ("Get temporary security credentials").
FROM amazon/aws-cli:2.38.4

ARG HELPER_VERSION=1.8.5
ARG HELPER_SHA256_X86_64=beec9ed1c492d93db809890f16713e3556353294b823c2184ad4e891f1b2b54d
ARG HELPER_SHA256_AARCH64=3d131aa888cd56da446f9c6bb460b1f0569f6c7edc74eae6193a2fe3928883ba

RUN set -e; \
    case "$(uname -m)" in \
      x86_64)  arch=X86_64;  sum=$HELPER_SHA256_X86_64 ;; \
      aarch64) arch=Aarch64; sum=$HELPER_SHA256_AARCH64 ;; \
      *) echo "no signing helper for $(uname -m)"; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/aws_signing_helper \
      "https://rolesanywhere.amazonaws.com/releases/$HELPER_VERSION/$arch/Linux/Amzn2023/aws_signing_helper"; \
    echo "$sum  /usr/local/bin/aws_signing_helper" | sha256sum -c -; \
    chmod +x /usr/local/bin/aws_signing_helper

COPY update-dns.sh /update-dns.sh
ENTRYPOINT ["/bin/sh", "/update-dns.sh"]
