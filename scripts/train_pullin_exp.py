from torch.utils.data import DataLoader
from transformers import DataCollatorWithPadding
from pytorch_lightning import Trainer
from pytorch_lightning.loggers import WandbLogger
import os
import warnings
from pytorch_lightning.callbacks import ModelCheckpoint

from transformers import BertModel, BertTokenizer
from abc import ABC
import pytorch_lightning as pl
import torch
from transformers import BertForTokenClassification, BertConfig, Adafactor, AdamW
from sklearn.metrics import balanced_accuracy_score, accuracy_score
import torch.nn.functional as F
from sklearn.multioutput import MultiOutputClassifier
from sklearn.metrics import label_ranking_average_precision_score, average_precision_score, f1_score

class TokenDataset(torch.utils.data.Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in
                self.encodings.items()}  # keys are input_ids, token_type_ids, attention_mask, labels, values are stored as a list of lists
        item['labels'] = torch.tensor(self.labels[idx])
        return item

    def __len__(self):
        return (len(self.labels))


class newDataset(torch.utils.data.Dataset):
    def __init__(self, dataframe):
        self.encodings = {"input_ids": dataframe["input_ids"], "token_type_ids": dataframe["token_type_ids"],
                          "attention_mask": dataframe["attention_mask"]}
        self.labels = {"labels": dataframe["labels"]}

    def __getitem__(self, idx):
        item = {"input_ids": self.encodings["input_ids"][idx], "token_type_ids": self.encodings["token_type_ids"][idx],
                "attention_mask": self.encodings["attention_mask"][idx], "labels": self.labels["labels"][idx]}
        return item

    def __len__(self):
        return (len(self.labels["labels"]))

class BertTokClassification(pl.LightningModule, ABC):
    def __init__(
            self,
            config: BertConfig = None,
            pretrained_dir: str = None,
            use_adafactor: bool = False,
            learning_rate=3e-5,
            **kwargs
    ):
        super().__init__()
        self.learning_rate = learning_rate
        self.use_adafactor = use_adafactor
        if pretrained_dir is None:
            self.bert = BertForTokenClassification(config, **kwargs)
        else:
            self.bert = BertForTokenClassification.from_pretrained(pretrained_dir, **kwargs)

    def forward(self, input_ids, attention_mask, labels):
        return self.bert(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

    def training_step(self, batch, batch_idx):
        input_ids = batch["input_ids"]
        attention_mask = batch["attention_mask"]
        labels = batch["labels"]
        outputs = self(input_ids=input_ids.to(self.device), attention_mask=attention_mask.to(self.device), labels=labels.to(self.device, dtype=torch.int64))
        loss = outputs.loss


        def get_acc(labels, logits):
            sumList = []
            for i in range(len(labels)):
                y_pred = torch.max(logits[i], 1).indices
                score = accuracy_score(labels[i], y_pred)
                sumList.append(score)
            avg = sum(sumList) / len(labels)
            return avg


        accuracy1 = get_acc(labels.cpu(), outputs.logits.cpu())

        # accuracy = balanced_accuracy_score(master[0], master[1])
        self.log(
            "train_batch_accuracy",
            accuracy1,
            on_step=True,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )
        self.log(
            "train_loss", loss, on_step=True, on_epoch=True, prog_bar=True, logger=True
        )
        return {"loss": loss}

    def validation_step(self, batch, batch_idx):
        input_ids = batch["input_ids"]
        attention_mask = batch["attention_mask"]
        labels = batch["labels"]
        outputs = self(input_ids=input_ids.to(self.device), attention_mask=attention_mask.to(self.device), labels=labels.to(self.device, dtype=torch.int64))
        loss = outputs.loss
        self.log(
            "val_loss", loss, on_step=True, on_epoch=True, prog_bar=True, logger=True
        )

        # def get_balanced_accuracy(labels, logits):
        #     y_pred = torch.max(logits, 1).indices
        #     score = balanced_accuracy_score(labels, y_pred)
        #     return score
        #
        # def label_average_precision(labels, logits):
        #     y_pred = torch.max(prob, 1).indices
        #     score = label_ranking_average_precision_score(labels, y_pred)
        #     return score
        # def f1_calc(labels, logits):
        #     sumList = []
        #     for i in range(len(labels)):
        #         y_pred = torch.max(logits[i], 1).indices
        #         score = f1_score(labels[i], y_pred, average='macro')
        #         sumList.append(score)
        #     avg = sum(sumList) / len(labels)
        #     return avg




        # """
        # 1. Iterate over the batch:
        #     for each_label in labels.cpu():
        #         shorten the length of the list to its true length using attention list and the function [true_length]
        #
        #     for each_logit in outputs.logits.cpu():
        #         use torch.max(outputs.logits.cpu()[0], 1) to get the indices for each logit (best label prediction)
        #         shorten the indices to its proper label length
        #         compare the indices to the labels
        # """
        def true_length(y_attention_mask):  # finds the start and stop of the actual sequence
            switch = False
            start = 0
            stop = 0
            counter = 0
            attention_mask = list(y_attention_mask)
            for i in attention_mask:
                if int(i) == 1 and switch == False:
                    switch = True
                    start = counter
                elif int(i) == 0 and switch == True:
                    stop = counter
                    break
                elif counter == 511:
                    stop = 512
                counter += 1
            return (start, stop)


        def short_clean(attention_mask, labels, logits): #attention_mask, labels.cpu(), outputs.logits.cpu()

            def true_length(y_attention_mask):  # finds the start and stop of the actual sequence
                switch = False
                start = 0
                stop = 0
                counter = 0
                attention_mask = list(y_attention_mask)
                for i in attention_mask:
                    if int(i) == 1 and switch == False:
                        switch = True
                        start = counter
                    elif int(i) == 0 and switch == True:
                        stop = counter
                        break
                    counter += 1
                return (start, stop)

            masterPred = []
            masterTrue = []
            for batch_index in range(len(labels)):
                real_len = true_length(attention_mask[batch_index])
                predIndecies = torch.max(outputs.logits.cpu()[batch_index], 1).indices
                start = real_len[0]
                stop = real_len[1]
                currentTrue = torch.LongTensor(labels[batch_index][start:stop])
                currentPred = torch.LongTensor(predIndecies[start:stop])
                if len(currentTrue) == 0:
                    masterTrue.append(currentTrue.tolist())
                    masterPred.append(currentPred.tolist())
                    print(f"CURRENT-PRED LEN: {len(currentPred)}")
                    print(f"CURRENT-TRUE LEN:{len(currentTrue)}")

            return (masterTrue, masterPred)

        master = short_clean(attention_mask, labels.cpu(), outputs.logits.cpu())
        print("###################################")
        print(f"MASTER-TRUE: {master[0]}")
        print("###################################")
        print(f"MASTER-PRED: {master[1]}")
        print("###################################")
        print("=======")
        print(f"LABEL LEN: {len(labels.cpu())}")
        for i in range(len(labels.cpu())):
            print(f"SINGLE LABEL LEN: {len(labels.cpu()[i])}")
            print(f"ATTENTION LEN: {len(attention_mask[i])}")
            #print(attention_mask[i])
            print(f"TRUE LEN: {true_length(attention_mask[i])}")
        print("=======")
        print(f"LABELS: {labels.cpu()}")
        print("||||||||||||||||||||||||||||")
        print("=======")
        print(f"LOGITS LEN: {len(outputs.logits.cpu())}")
        for i in outputs.logits.cpu():
            print(f"SINGLE LOGIT LEN: {len(i)}")
        print(f"LOGIT SINGLE LIST LEN: {len(outputs.logits.cpu()[0][0])}")
        b_logit = torch.max(outputs.logits.cpu()[0], 1)
        b_logit_indices = torch.max(outputs.logits.cpu()[0], 1).indices
        print(f"LOGIT BEST: {b_logit}")
        print(f"LOGIT BEST INDICES: {b_logit_indices}")
        print("=======")
        print(f"LOGITS: {outputs.logits.cpu()}")

        # accuracy = label_average_precision(labels.cpu(), logits=outputs.logits.cpu()) #replaced get_balanced_accuracy(labels.cpu(), logits=outputs.logits.cpu()) with label ranking average precision

        # def balanced_accuracy_score(labels, logits):
        #     sumList = []
        #     for i in range(len(labels)):
        #         y_predList = []
        #         trueList = []
        #         y_pred = logits[i]
        #         previous = 0
        #         for lab in labels[i]:
        #             if lab == -100:
        #                 y_predList.append(y_pred[previous])
        #                 trueList.append(previous)
        #             else:
        #                 previous = lab
        #                 y_predList.append(y_pred[previous])
        #                 trueList.append(previous)
        #
        #         num = average_precision_score(trueList, y_predList)
        #         sumList.append(num)
        #     big = sum(sumList) / len(labels)
        #     return big

        def get_bal_acc(labels, logits):
            sumList = []
            for i in range(len(labels)):
                y_pred = torch.max(logits[i], 1).indices
                score = balanced_accuracy_score(labels[i], y_pred)
                sumList.append(score)
            avg = sum(sumList) / len(labels)
            return avg

        accuracy = get_bal_acc(labels.cpu(), outputs.logits.cpu())

        self.log(
            "val_accuracy",
            accuracy,
            on_step=False,
            on_epoch=True,
            prog_bar=True,
            logger=True,
        )
        return {"val_loss": loss}

    def configure_optimizers(self):
        if self.use_adafactor:
            return Adafactor(
                self.parameters(),
                lr=self.learning_rate,
                eps=(1e-30, 1e-3),
                clip_threshold=1.0,
                decay_rate=-0.8,
                beta1=None,
                weight_decay=0.0,
                relative_step=False,
                scale_parameter=False,
                warmup_init=False)
        else:
            return AdamW(self.parameters(), lr=self.learning_rate)

    def save_pretrained(self, pretrained_dir):
        self.bert.save_pretrained(self, prtrained_dir)

    def predict_classes(self, input_ids, attention_mask, return_logits=False):
        output = self.bert(input_ids=input_ids.to(self.device), attention_mask=attention_mask)
        if return_logits:
            return output.logits
        else:
            probabilities = F.sigmoid(output.logits)
            predictions = torch.argmax(probabilities)
            return {"probabilities": probabilities, "predictions": predictions}

    def get_attention(self, input_ids, attention_mask, specific_attention_head: int = None):
        output = self.bert(inputs_ids=input_ids.to(self.device), attention_mask=attention_mask)
        if specific_attention_head is not None:
            last_layer = output.attentions[-1]  # grabs the last layer
            all_last_attention_heads = [torch.max(this_input[specific_attention_head], axis=0)[0].indices for this_input in last_layer]
            return all_last_attention_heads
        return output.attentions


def main():

    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["MKL_THREADING_LAYER"] = "GNU"
    os.environ["TOKENIZERS_PARALLELISM"] = "true"

    #######################################
    num_cpu = 16
    lr = '6.4937e-05'
    wandb_name = f"pullin>1000-{lr}-mxepch10"
    num_labels = 61
    max_epch = 10
    gpus = '1, 2, 3'

    data_folder = "pullin_parsed_data"
    strat_train_name = "embedding_pullin_noDupes_train>1000_stratified_domainPiece.pt"
    strat_val_name = "embedding_pullin_noDupes_val>1000_stratified_domainPiece.pt"

    model_folder = "pullin"
    save_checkpoint_name = "pullin_max_epch99.ckpt"

    ###-dont need to touch-###
    save_checkpoint_path = f"/mnt/storage/grid/home/eric/hmm2bert/models/{model_folder}/{save_checkpoint_name}"
    strat_train_path = f"/mnt/storage/grid/home/eric/hmm2bert/{data_folder}/{strat_train_name}"
    strat_val_path = f"/mnt/storage/grid/home/eric/hmm2bert/{data_folder}/{strat_val_name}"
    ###

    #######################################

    #load tokenizer and wandb logger

    wandb_logger = WandbLogger(name=wandb_name, project="hmm_reBERT")
    tokenizer = BertTokenizer.from_pretrained("Rostlab/prot_bert", do_lower_case=False)

    #load train and test tensors and instantiate pytorch lightning wrapper for the huggingface model with the base pretrained protbert model

    encoded_train = torch.load(strat_train_path)
    encoded_test = torch.load(strat_val_path)
    bsc = BertTokClassification(pretrained_dir='Rostlab/prot_bert', use_adafactor=True, num_labels=num_labels)

    #setup checkpoint callback

    checkpoint_callback = ModelCheckpoint(
        monitor='val_loss_epoch',
        dirpath=f'/mnt/storage/grid/home/eric/hmm2bert/models/{model_folder}',
        filename='pullin>1000_best_loss',
        save_top_k=3,
        mode='min'
    )

    #setup data collator, trainer, and dataloader for train and val dataset

    data_collator = DataCollatorWithPadding(tokenizer=tokenizer)
    trainer = Trainer(
        max_epochs=max_epch,
        gpus=gpus,
        auto_lr_find=False,
        logger=wandb_logger,
        accelerator="ddp",
        callbacks=[checkpoint_callback]
    ) #
    warnings.filterwarnings("ignore")

    train_dl = DataLoader(encoded_train, batch_size=4, num_workers=num_cpu, collate_fn=data_collator, shuffle=True)
    eval_dl = DataLoader(encoded_test, batch_size=4, num_workers=num_cpu, collate_fn=data_collator, shuffle=False)

    #train and save classifier as checkpoint
    #trainer.tune(bsc, train_dataloader=[train_dl], val_dataloaders=[eval_dl])
    trainer.fit(bsc, train_dataloader=train_dl, val_dataloaders=eval_dl)
    #trainer.save_checkpoint(save_checkpoint_path)


if __name__ == '__main__':
    main()