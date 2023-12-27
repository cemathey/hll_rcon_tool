"""audit_log

Revision ID: ffdd9ef91ad9
Revises: e74091f5f534
Create Date: 2022-12-23 13:51:29.366026

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ffdd9ef91ad9"
down_revision = "e74091f5f534"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("creation_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("command_arguments", sa.String(), nullable=True),
        sa.Column("command_result", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_log_username"), "audit_log", ["username"], unique=False
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_audit_log_username"), table_name="audit_log")
    op.drop_table("audit_log")
    # ### end Alembic commands ###
