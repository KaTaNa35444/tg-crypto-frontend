"""Initial schema, including durable delivery leases and immutable evidence versions."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('users',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('language',sa.String(2),nullable=False),
        sa.Column('timezone',sa.String(80),nullable=False),sa.Column('notifications',sa.Boolean(),nullable=False),
        sa.Column('blocked',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('subscriptions',sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('exchange',sa.String(16),primary_key=True))
    op.create_table('sources',sa.Column('exchange',sa.String(16),primary_key=True),
        sa.Column('initialized',sa.Boolean(),nullable=False),sa.Column('last_success',sa.DateTime(timezone=True)),
        sa.Column('last_attempt',sa.DateTime(timezone=True)),sa.Column('last_error',sa.Text()))
    op.create_table('promotions',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('exchange',sa.String(16),nullable=False),sa.Column('source_id',sa.String(180),nullable=False),
        sa.Column('canonical_url',sa.Text(),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('data',sa.JSON()),sa.Column('candidate',sa.JSON()),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('discovered_at',sa.DateTime(timezone=True),nullable=False),sa.Column('checked_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('baseline',sa.Boolean(),nullable=False),sa.Column('demo',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('exchange','source_id'),sa.UniqueConstraint('exchange','canonical_url'))
    op.create_table('versions',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('promotion_id',sa.Integer(),sa.ForeignKey('promotions.id'),nullable=False),
        sa.Column('data',sa.JSON(),nullable=False),sa.Column('event',sa.String(20),nullable=False),
        sa.Column('actor',sa.BigInteger()),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('saved',sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('promotion_id',sa.Integer(),sa.ForeignKey('promotions.id'),primary_key=True))
    op.create_table('reminders',sa.Column('user_id',sa.BigInteger(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('promotion_id',sa.Integer(),sa.ForeignKey('promotions.id'),primary_key=True),
        sa.Column('minutes',sa.Integer(),nullable=False),sa.Column('due_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('deadline',sa.String(50),nullable=False),sa.Column('active',sa.Boolean(),nullable=False))
    op.create_table('jobs',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('key',sa.String(200),nullable=False,unique=True),
        sa.Column('user_id',sa.BigInteger(),nullable=False),sa.Column('promotion_id',sa.Integer(),sa.ForeignKey('promotions.id')),
        sa.Column('kind',sa.String(20),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('attempts',sa.Integer(),nullable=False),sa.Column('available_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('lease_until',sa.DateTime(timezone=True)),sa.Column('lease_token',sa.String(36)),
        sa.Column('expected_deadline',sa.String(50)),sa.Column('error',sa.String(100)))
    op.create_index('ix_jobs_status','jobs',['status'])
    op.create_index('ix_jobs_available_at','jobs',['available_at'])

def downgrade():
    for table in ['jobs','reminders','saved','versions','promotions','sources','subscriptions','users']:
        op.drop_table(table)
