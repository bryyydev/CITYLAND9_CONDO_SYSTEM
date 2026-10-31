SET XACT_ABORT ON;
DECLARE @add_condo_payer BIT = CASE WHEN COL_LENGTH(N'dbo.unit', N'condo_charge_payer') IS NULL THEN 1 ELSE 0 END;
DECLARE @add_water_payer BIT = CASE WHEN COL_LENGTH(N'dbo.unit', N'water_charge_payer') IS NULL THEN 1 ELSE 0 END;
DECLARE @add_parking_payer BIT = CASE WHEN COL_LENGTH(N'dbo.unit', N'parking_charge_payer') IS NULL THEN 1 ELSE 0 END;
DECLARE @add_storage_payer BIT = CASE WHEN COL_LENGTH(N'dbo.unit', N'storage_charge_payer') IS NULL THEN 1 ELSE 0 END;
DECLARE @add_other_payer BIT = CASE WHEN COL_LENGTH(N'dbo.unit', N'other_charge_payer') IS NULL THEN 1 ELSE 0 END;
BEGIN TRY
    BEGIN TRANSACTION;

    IF COL_LENGTH(N'dbo.unit', N'condo_charge_payer') IS NULL
        ALTER TABLE dbo.[unit] ADD condo_charge_payer VARCHAR(10) NOT NULL CONSTRAINT DF_unit_condo_charge_payer DEFAULT ('Owner');
    IF COL_LENGTH(N'dbo.unit', N'water_charge_payer') IS NULL
        ALTER TABLE dbo.[unit] ADD water_charge_payer VARCHAR(10) NOT NULL CONSTRAINT DF_unit_water_charge_payer DEFAULT ('Owner');
    IF COL_LENGTH(N'dbo.unit', N'parking_charge_payer') IS NULL
        ALTER TABLE dbo.[unit] ADD parking_charge_payer VARCHAR(10) NOT NULL CONSTRAINT DF_unit_parking_charge_payer DEFAULT ('Owner');
    IF COL_LENGTH(N'dbo.unit', N'storage_charge_payer') IS NULL
        ALTER TABLE dbo.[unit] ADD storage_charge_payer VARCHAR(10) NOT NULL CONSTRAINT DF_unit_storage_charge_payer DEFAULT ('Owner');
    IF COL_LENGTH(N'dbo.unit', N'other_charge_payer') IS NULL
        ALTER TABLE dbo.[unit] ADD other_charge_payer VARCHAR(10) NOT NULL CONSTRAINT DF_unit_other_charge_payer DEFAULT ('Owner');

    EXEC sys.sp_executesql
        N'UPDATE dbo.[unit]
          SET condo_charge_payer = CASE WHEN @add_condo_payer = 1 THEN CASE WHEN occupancy_type = ''Tenant'' THEN ''Tenant'' ELSE ''Owner'' END ELSE condo_charge_payer END,
              water_charge_payer = CASE WHEN @add_water_payer = 1 THEN CASE WHEN occupancy_type = ''Tenant'' THEN ''Tenant'' ELSE ''Owner'' END ELSE water_charge_payer END,
              parking_charge_payer = CASE WHEN @add_parking_payer = 1 THEN CASE WHEN occupancy_type = ''Tenant'' THEN ''Tenant'' ELSE ''Owner'' END ELSE parking_charge_payer END,
              storage_charge_payer = CASE WHEN @add_storage_payer = 1 THEN CASE WHEN occupancy_type = ''Tenant'' THEN ''Tenant'' ELSE ''Owner'' END ELSE storage_charge_payer END,
              other_charge_payer = CASE WHEN @add_other_payer = 1 THEN CASE WHEN occupancy_type = ''Tenant'' THEN ''Tenant'' ELSE ''Owner'' END ELSE other_charge_payer END;',
        N'@add_condo_payer BIT, @add_water_payer BIT, @add_parking_payer BIT, @add_storage_payer BIT, @add_other_payer BIT',
        @add_condo_payer, @add_water_payer, @add_parking_payer, @add_storage_payer, @add_other_payer;

    IF COL_LENGTH(N'dbo.billing', N'condo_charge_payer') IS NULL
        ALTER TABLE dbo.billing ADD condo_charge_payer VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.billing', N'water_charge_payer') IS NULL
        ALTER TABLE dbo.billing ADD water_charge_payer VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.billing', N'parking_charge_payer') IS NULL
        ALTER TABLE dbo.billing ADD parking_charge_payer VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.billing', N'storage_charge_payer') IS NULL
        ALTER TABLE dbo.billing ADD storage_charge_payer VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.billing', N'other_charge_payer') IS NULL
        ALTER TABLE dbo.billing ADD other_charge_payer VARCHAR(10) NULL;

    EXEC sys.sp_executesql N'
        UPDATE b
        SET condo_charge_payer = COALESCE(b.condo_charge_payer, u.condo_charge_payer),
            water_charge_payer = COALESCE(b.water_charge_payer, u.water_charge_payer),
            parking_charge_payer = COALESCE(b.parking_charge_payer, u.parking_charge_payer),
            storage_charge_payer = COALESCE(b.storage_charge_payer, u.storage_charge_payer),
            other_charge_payer = COALESCE(b.other_charge_payer, u.other_charge_payer)
        FROM dbo.billing AS b
        JOIN dbo.[unit] AS u ON u.id = b.unit_id
        WHERE b.condo_charge_payer IS NULL OR b.water_charge_payer IS NULL
           OR b.parking_charge_payer IS NULL OR b.storage_charge_payer IS NULL
           OR b.other_charge_payer IS NULL;';

    IF COL_LENGTH(N'dbo.payment', N'payer_type') IS NULL
        ALTER TABLE dbo.payment ADD payer_type VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.advance_payment', N'payer_type') IS NULL
        ALTER TABLE dbo.advance_payment ADD payer_type VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.water_reading', N'charge_payer') IS NULL
        ALTER TABLE dbo.water_reading ADD charge_payer VARCHAR(10) NULL;
    IF COL_LENGTH(N'dbo.receipt_allocation', N'payer_type') IS NULL
        ALTER TABLE dbo.receipt_allocation ADD payer_type VARCHAR(10) NULL;

    EXEC sys.sp_executesql N'
        UPDATE dbo.advance_payment
        SET payer_type = ''Tenant''
        WHERE payment_date < ''20261001'' AND reversed_at IS NULL AND payer_type IS NULL;';

    EXEC sys.sp_executesql N'
        UPDATE wr SET charge_payer = u.water_charge_payer
        FROM dbo.water_reading AS wr
        JOIN dbo.[unit] AS u ON u.id = wr.unit_id
        WHERE wr.charge_payer IS NULL;';

    EXEC sys.sp_executesql N'
        UPDATE ra SET payer_type = p.payer_type
        FROM dbo.receipt_allocation AS ra
        JOIN dbo.payment AS p ON p.id = ra.payment_id
        WHERE ra.kind = ''bill'' AND ra.payer_type IS NULL;';

    EXEC sys.sp_executesql N'
        UPDATE ra SET payer_type = wr.charge_payer
        FROM dbo.receipt_allocation AS ra
        JOIN dbo.water_reading AS wr ON wr.id = ra.water_reading_id
        WHERE ra.kind = ''water'' AND ra.payer_type IS NULL;';

    EXEC sys.sp_executesql N'
        UPDATE ra SET payer_type = ap.payer_type
        FROM dbo.receipt_allocation AS ra
        JOIN dbo.advance_payment AS ap ON ap.id = ra.advance_payment_id
        WHERE ra.kind = ''advance'' AND ra.payer_type IS NULL;';

    IF OBJECT_ID(N'dbo.billing_extra_charge', N'U') IS NULL
        EXEC sys.sp_executesql N'CREATE TABLE dbo.billing_extra_charge (
            id INT IDENTITY(1,1) NOT NULL,
            billing_id INT NOT NULL,
            description VARCHAR(100) NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            payer_type VARCHAR(10) NOT NULL,
            updated_at DATETIME2(6) NULL,
            updated_by VARCHAR(80) NULL,
            CONSTRAINT PK_billing_extra_charge PRIMARY KEY (id),
            CONSTRAINT FK_billing_extra_charge_billing FOREIGN KEY (billing_id) REFERENCES dbo.billing(id),
            CONSTRAINT ck_billing_extra_charge_amount_nonneg CHECK (amount >= 0),
            CONSTRAINT ck_billing_extra_charge_payer CHECK (payer_type IN (''Owner'', ''Tenant''))
        );';
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_billing_extra_charge_billing_id'
                   AND object_id = OBJECT_ID(N'dbo.billing_extra_charge'))
        EXEC sys.sp_executesql N'CREATE INDEX ix_billing_extra_charge_billing_id ON dbo.billing_extra_charge(billing_id);';

    IF OBJECT_ID(N'dbo.party_opening_balance', N'U') IS NULL
        EXEC sys.sp_executesql N'CREATE TABLE dbo.party_opening_balance (
            id INT IDENTITY(1,1) NOT NULL,
            unit_id INT NOT NULL,
            payer_type VARCHAR(10) NOT NULL,
            as_of_month VARCHAR(7) NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            CONSTRAINT PK_party_opening_balance PRIMARY KEY (id),
            CONSTRAINT FK_party_opening_balance_unit FOREIGN KEY (unit_id) REFERENCES dbo.[unit](id),
            CONSTRAINT uq_party_opening_balance UNIQUE (unit_id, payer_type, as_of_month),
            CONSTRAINT ck_party_opening_balance_payer CHECK (payer_type IN (''Owner'', ''Tenant''))
        );';
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_party_opening_balance_unit_id'
                   AND object_id = OBJECT_ID(N'dbo.party_opening_balance'))
        EXEC sys.sp_executesql N'CREATE INDEX ix_party_opening_balance_unit_id ON dbo.party_opening_balance(unit_id);';

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
